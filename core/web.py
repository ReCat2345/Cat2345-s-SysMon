"""HTTP 服务：静态页面、JSON API、登录鉴权、安全响应头。"""

from __future__ import annotations

import hmac
import json
import mimetypes
import re
import sys
import threading
import time
import traceback
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from . import auth
from .branding import BrandingStore, limit_for, sniff_image
from .collectors import Collector
from .config import MODULES, PALETTES, ConfigError, Store

MAX_BODY = 64 * 1024
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,32}$")

CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class AppContext:
    def __init__(
        self,
        store: Store,
        collector: Collector,
        branding: BrandingStore,
        static_dir: Path,
        data_dir: Path,
        setup_token: Optional[str],
        version: str,
    ):
        self.store = store
        self.collector = collector
        self.branding = branding
        self.static_dir = Path(static_dir)
        self.data_dir = Path(data_dir)
        self.setup_token = setup_token
        self.version = version
        self.sessions = auth.Sessions()
        self.throttle = auth.Throttle()
        self.setup_lock = threading.Lock()
        self.started_at = time.time()


class Handler(BaseHTTPRequestHandler):
    server_version = "SysMon"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 30  # 防止半开连接长期占用线程

    ctx: AppContext  # 由 make_server 注入

    # ------------------------------------------------------------------ 基础
    def log_message(self, fmt: str, *args: Any) -> None:
        if self.path.startswith("/api/metrics"):
            return
        sys.stderr.write("[%s] %s %s\n" % (time.strftime("%H:%M:%S"), self.address_string(), fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            self._route_get(path)
        except ApiError as exc:
            self._json({"error": str(exc)}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            traceback.print_exc()
            self._json({"error": "服务器内部错误"}, 500)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            self._route_post(path)
        except ApiError as exc:
            self.close_connection = True
            self._json({"error": str(exc)}, exc.status, headers=[("Connection", "close")])
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            traceback.print_exc()
            self.close_connection = True
            self._json({"error": "服务器内部错误"}, 500, headers=[("Connection", "close")])

    # ------------------------------------------------------------------ 路由
    def _route_get(self, path: str) -> None:
        if path == "/":
            self._send_file(self.ctx.static_dir / "index.html", "text/html; charset=utf-8")
        elif path.startswith("/static/"):
            self._send_static(path)
        elif path in ("/branding/icon", "/branding/wallpaper"):
            self._serve_branding(path.rsplit("/", 1)[1])
        elif path == "/api/state":
            self._json(self._state_payload())
        elif path == "/api/metrics":
            # 只读指标对访客开放；修改类接口仍然需要登录
            self._json(self._metrics_payload())
        elif path == "/healthz":
            self._json({"ok": True})
        elif path == "/favicon.ico":
            self._empty(204)
        elif path.startswith("/api/"):
            raise ApiError(404, "接口不存在")
        else:
            self._empty(404)

    def _route_post(self, path: str) -> None:
        self._check_origin()
        if path == "/api/login":
            self._api_login()
        elif path == "/api/setup":
            self._api_setup()
        elif path == "/api/logout":
            self._api_logout()
        elif path == "/api/config":
            self._api_config()
        elif path == "/api/gpu/rescan":
            self._api_gpu_rescan()
        elif path == "/api/password":
            self._api_password()
        elif path.startswith("/api/branding/"):
            self._api_branding(path)
        else:
            raise ApiError(404, "接口不存在")

    # ------------------------------------------------------------------ 鉴权
    def _cookie_token(self) -> Optional[str]:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except CookieError:
            return None
        morsel = jar.get(auth.COOKIE_NAME)
        return morsel.value if morsel else None

    def _session(self) -> Optional[Dict[str, Any]]:
        return self.ctx.sessions.get(self._cookie_token() or "")

    def _require_session(self) -> Dict[str, Any]:
        session = self._session()
        if not session:
            raise ApiError(401, "未登录或会话已过期")
        return session

    def _ip(self) -> str:
        return self.client_address[0] if self.client_address else "unknown"

    def _secure_cookie(self) -> bool:
        return (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"

    def _session_cookie(self, token: str) -> Tuple[str, str]:
        parts = [
            f"{auth.COOKIE_NAME}={token}",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            f"Max-Age={auth.SESSION_TTL}",
        ]
        if self._secure_cookie():
            parts.append("Secure")
        return ("Set-Cookie", "; ".join(parts))

    def _clear_cookie(self) -> Tuple[str, str]:
        return ("Set-Cookie", f"{auth.COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")

    def _check_origin(self) -> None:
        origin = self.headers.get("Origin")
        if not origin:
            return
        host = self.headers.get("Host") or ""
        if urlparse(origin).netloc != host:
            raise ApiError(403, "跨站请求被拒绝")

    # ------------------------------------------------------------------ API
    def _caps(self) -> Dict[str, Any]:
        gpu = self.ctx.collector.gpu.snapshot()
        return {
            "gpu": {
                "supported": gpu["supported"],
                "vendor": gpu.get("vendor", ""),
                "reason": gpu["reason"],
                "device_count": len(gpu["devices"]),
            },
            "limits": {"min_refresh": 2, "max_refresh": 60},
        }

    def _base_payload(self, authenticated: bool, username: Optional[str] = None) -> Dict[str, Any]:
        """访客与管理员共用同一份只读数据，只有 authenticated 不同。"""
        ctx = self.ctx
        payload: Dict[str, Any] = {
            "authenticated": authenticated,
            "needs_setup": not ctx.store.configured,
            "version": ctx.version,
            "settings": ctx.store.settings(),
            "catalog": MODULES,
            "palettes": [
                {"id": key, "name": value["name"], "primary": value["primary"]}
                for key, value in PALETTES.items()
            ],
            "capabilities": self._caps(),
            "host": ctx.collector.host,
            "assets": ctx.branding.assets(),
            "metrics": ctx.collector.snapshot(),
            "server_time": time.time(),
        }
        if username:
            payload["username"] = username
        return payload

    def _state_payload(self) -> Dict[str, Any]:
        session = self._session()
        admin = self.ctx.store.admin() or {}
        return self._base_payload(bool(session), admin.get("username") if session else None)

    def _metrics_payload(self) -> Dict[str, Any]:
        return {
            "metrics": self.ctx.collector.snapshot(),
            "settings": self.ctx.store.settings(),
            "capabilities": self._caps(),
            "assets": self.ctx.branding.assets(),
            "server_time": time.time(),
        }

    def _api_login(self) -> None:
        body = self._json_body()
        ip = self._ip()
        allowed, retry = self.ctx.throttle.check(ip)
        if not allowed:
            raise ApiError(429, f"尝试次数过多，请 {retry} 秒后再试")
        record = self.ctx.store.admin()
        if not record:
            raise ApiError(409, "尚未初始化管理员账户")
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        if not auth.verify_password(record, username, password):
            self.ctx.throttle.failure(ip)
            raise ApiError(401, "用户名或密码错误")
        self.ctx.throttle.success(ip)
        token = self.ctx.sessions.create(ip, self.headers.get("User-Agent") or "")
        self._json(
            self._base_payload(True, record.get("username")),
            headers=[self._session_cookie(token)],
        )

    def _api_setup(self) -> None:
        body = self._json_body()
        with self.ctx.setup_lock:
            if self.ctx.store.configured:
                raise ApiError(409, "管理员账户已存在")
            expected = self.ctx.setup_token
            if not expected:
                raise ApiError(409, "初始化阶段已结束")
            token = str(body.get("token") or "").strip()
            if not hmac.compare_digest(token, expected):
                raise ApiError(403, "初始化令牌不正确")
            username = str(body.get("username") or "").strip()
            password = str(body.get("password") or "")
            if not USERNAME_RE.match(username):
                raise ApiError(400, "用户名需为 3–32 位字母、数字、下划线、点或短横线")
            if not (8 <= len(password) <= 128):
                raise ApiError(400, "密码长度需为 8–128 位")
            record = auth.hash_password(password, username)
            self.ctx.store.set_admin(record)
            auth.remove_setup_token(self.ctx.data_dir / "setup-token.txt")
            self.ctx.setup_token = None
        session_token = self.ctx.sessions.create(self._ip(), self.headers.get("User-Agent") or "")
        self._json(
            self._base_payload(True, username),
            headers=[self._session_cookie(session_token)],
        )

    def _api_logout(self) -> None:
        self._drain_body()
        token = self._cookie_token()
        if token:
            self.ctx.sessions.destroy(token)
        self._json({"ok": True}, headers=[self._clear_cookie()])

    def _api_config(self) -> None:
        self._require_session()
        body = self._json_body()
        raw = body.get("settings")
        if not isinstance(raw, dict):
            raise ApiError(400, "缺少 settings 对象")
        try:
            settings = self.ctx.store.save_settings(raw)
        except ConfigError as exc:
            raise ApiError(exc.status, str(exc)) from exc
        self._json({"settings": settings, "capabilities": self._caps()})

    def _api_gpu_rescan(self) -> None:
        self._require_session()
        self._drain_body()
        self.ctx.collector.rescan_gpu()
        supported = self.ctx.collector.gpu.supported
        self.ctx.store.set_gpu_supported(supported)
        self._json(
            {
                "capabilities": self._caps(),
                "settings": self.ctx.store.settings(),
            }
        )

    def _api_password(self) -> None:
        session = self._require_session()
        body = self._json_body()
        record = self.ctx.store.admin() or {}
        current = str(body.get("current") or "")
        new_password = str(body.get("new") or "")
        if not auth.verify_password(record, record.get("username", ""), current):
            raise ApiError(401, "当前密码不正确")
        if not (8 <= len(new_password) <= 128):
            raise ApiError(400, "新密码长度需为 8–128 位")
        if new_password == current:
            raise ApiError(400, "新密码不能与当前密码相同")
        self.ctx.store.set_admin(auth.hash_password(new_password, record.get("username") or "admin"))
        self.ctx.sessions.destroy_others(session["token"])
        self._json({"ok": True})

    # ------------------------------------------------------------------ 品牌资源
    def _api_branding(self, path: str) -> None:
        parts = [segment for segment in path.strip("/").split("/") if segment]
        if len(parts) < 3 or parts[0] != "api" or parts[1] != "branding":
            raise ApiError(404, "接口不存在")
        kind = parts[2]
        if kind not in ("icon", "wallpaper"):
            raise ApiError(404, "资源类型不存在")
        self._require_session()

        if len(parts) >= 4:
            if parts[3] != "remove" or len(parts) > 4:
                raise ApiError(404, "接口不存在")
            self._drain_body()
            self.ctx.branding.remove(kind)
            self._json({"ok": True, "assets": self.ctx.branding.assets()})
            return

        data = self._read_image(limit_for(kind))
        mime = sniff_image(data)
        if mime is None:
            raise ApiError(415, "仅支持 PNG / JPEG / WebP 图片")
        if kind == "wallpaper" and mime == "image/x-icon":
            raise ApiError(415, "壁纸不支持 ICO 格式")
        self.ctx.branding.save(kind, data, mime)
        self._json({"ok": True, "assets": self.ctx.branding.assets()})

    def _serve_branding(self, kind: str) -> None:
        asset = self.ctx.branding.get(kind)
        if not asset:
            raise ApiError(404, "资源不存在")
        path, mime = asset
        try:
            data = path.read_bytes()
        except OSError:
            raise ApiError(404, "资源不存在")
        self._send(200, data, mime)

    def _read_image(self, limit: int) -> bytes:
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype and ctype != "application/octet-stream" and not ctype.startswith("image/"):
            raise ApiError(415, "请上传图片文件")
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0:
            raise ApiError(400, "请求体为空")
        if length > limit:
            raise ApiError(413, f"文件过大，上限 {limit // 1024} KB")
        data = self.rfile.read(length)
        if len(data) != length:
            raise ApiError(400, "上传被中断")
        return data

    # ------------------------------------------------------------------ 请求体
    def _drain_body(self) -> None:
        """读取并丢弃请求体，避免 keep-alive 连接上残留字节污染下一个请求。"""
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length > 0:
            try:
                self.rfile.read(min(length, MAX_BODY))
            except OSError:
                self.close_connection = True

    def _json_body(self) -> Dict[str, Any]:
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise ApiError(415, "请求需使用 application/json")
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0:
            raise ApiError(400, "请求体为空")
        if length > MAX_BODY:
            raise ApiError(413, "请求体过大")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "JSON 解析失败")
        if not isinstance(data, dict):
            raise ApiError(400, "请求体必须是 JSON 对象")
        return data

    # ------------------------------------------------------------------ 响应
    def _json(
        self,
        payload: Dict[str, Any],
        status: int = 200,
        headers: Optional[List[Tuple[str, str]]] = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", headers)

    def _empty(self, status: int) -> None:
        self.send_response(status)
        self._common_headers()
        if status not in (204, 304):
            self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(
        self,
        status: int,
        body: bytes,
        ctype: str,
        headers: Optional[List[Tuple[str, str]]] = None,
    ) -> None:
        self.send_response(status)
        self._common_headers()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for key, value in headers or []:
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _common_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        self.send_header("Content-Security-Policy", CSP)

    # ------------------------------------------------------------------ 静态文件
    def _send_static(self, path: str) -> None:
        rel = unquote(path[len("/static/"):])
        parts = Path(rel).parts
        if not rel or rel.startswith("/") or ".." in parts:
            raise ApiError(404, "文件不存在")
        root = self.ctx.static_dir.resolve()
        target = (root / rel).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ApiError(404, "文件不存在")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".js":
            ctype = "text/javascript; charset=utf-8"
        elif target.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif target.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        elif target.suffix == ".svg":
            ctype = "image/svg+xml"
        self._send(200, target.read_bytes(), ctype)

    def _send_file(self, path: Path, ctype: str) -> None:
        if not path.is_file():
            raise ApiError(404, "缺少前端文件")
        self._send(200, path.read_bytes(), ctype)


def make_handler(ctx: AppContext):
    class _BoundHandler(Handler):
        pass

    _BoundHandler.ctx = ctx
    return _BoundHandler


class MonitorServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
