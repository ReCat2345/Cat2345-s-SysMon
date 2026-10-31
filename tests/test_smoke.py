"""SysMon 冒烟测试：启动一个临时实例，走通初始化 / 登录 / 指标 / 配置。

用法：
    python tests/test_smoke.py

只依赖标准库与 psutil，不会影响正在运行的实例（使用随机端口和临时数据目录）。
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
failures = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        failures.append(name)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_data_dir() -> Path:
    """普通 mkdir（而非 mkdtemp）以继承目录权限，兼容受限的沙箱环境。"""
    name = f"sysmon-smoke-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    for base in (Path(tempfile.gettempdir()), ROOT):
        candidate = base / name
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except OSError:
            continue
    raise SystemExit("无法创建临时数据目录")


def main() -> int:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    data_dir = make_data_dir()
    log_path = data_dir / "server.log"
    log = log_path.open("w", encoding="utf-8")

    proc = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "app.py"),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--data-dir",
            str(data_dir),
        ],
        cwd=str(ROOT),
        stdout=log,
        stderr=subprocess.STDOUT,
    )

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(method: str, path: str, body=None):
        headers = {}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=payload, headers=headers, method=method)
        try:
            with opener.open(req, timeout=15) as resp:
                raw = resp.read()
                if not raw:
                    return resp.status, {}
                try:
                    return resp.status, json.loads(raw)
                except json.JSONDecodeError:
                    return resp.status, raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except Exception:
                return exc.code, {"error": raw.decode("utf-8", "replace")}

    try:
        deadline = time.time() + 25
        while time.time() < deadline:
            try:
                if call("GET", "/healthz")[0] == 200:
                    break
            except (urllib.error.URLError, ConnectionError):
                pass
            time.sleep(0.3)
        else:
            print("服务未能启动，日志：")
            print(log_path.read_text(encoding="utf-8", errors="replace")[-2000:])
            return 1

        token_file = data_dir / "setup-token.txt"
        token = token_file.read_text(encoding="utf-8").strip()

        status, state = call("GET", "/api/state")
        check("state 可访问", status == 200 and state.get("needs_setup") is True, str(state))
        check("品牌默认值", state.get("settings", {}).get("branding", {}).get("title") == "SysMon")
        check("配色预设数量", len(state.get("palettes", [])) == 5, str(len(state.get("palettes", []))))
        check("硬件信息字段", isinstance(state.get("metrics", {}).get("hardware"), dict))

        status, visitor = call("GET", "/api/metrics")
        check("访客可读指标", status == 200 and "metrics" in visitor, str(status))
        status, _ = call("POST", "/api/config", {"settings": {}})
        check("访客不能改配置", status == 401, str(status))

        status, state = call(
            "POST", "/api/setup", {"token": token, "username": "admin", "password": "smoke-test-123"}
        )
        check("初始化成功", status == 200 and state.get("authenticated") is True, str(status))
        check("初始化令牌已删除", not token_file.exists())

        status, body = call("GET", "/api/metrics")
        check("指标接口可用", status == 200, str(status))
        metrics = body.get("metrics", {}) if status == 200 else {}
        check("CPU 数据", isinstance(metrics.get("cpu", {}).get("percent"), (int, float)), str(metrics.get("cpu")))
        check("内存数据", metrics.get("memory", {}).get("total", 0) > 0, str(metrics.get("memory")))
        check("历史数据", len(metrics.get("history", {}).get("cpu", [])) >= 1)
        gpu_supported = bool(body.get("capabilities", {}).get("gpu", {}).get("supported"))

        settings = body["settings"]
        settings["branding"]["title"] = "冒烟测试"
        order = [m["id"] for m in settings["modules"]]
        reordered = sorted(settings["modules"], key=lambda m: (m["id"] != "net", order.index(m["id"])))
        for module in reordered:
            if module["id"] == "disk":
                module["visible"] = not module["visible"]
        settings["modules"] = reordered
        status, body = call("POST", "/api/config", {"settings": settings})
        check("保存设置", status == 200, str(status))
        saved = body.get("settings", {}) if status == 200 else {}
        check("顺序已生效", [m["id"] for m in saved.get("modules", [])][0] == "net", str(saved.get("modules")))
        check("标题已保存", saved.get("branding", {}).get("title") == "冒烟测试", str(saved.get("branding")))

        if not gpu_supported:
            settings["gpu_enabled"] = True
            status, _ = call("POST", "/api/config", {"settings": settings})
            check("无 GPU 时禁止启用", status == 409, str(status))
        else:
            print("SKIP  无 GPU 场景（当前主机检测到 GPU）")

        status, _ = call("POST", "/api/logout", {})
        check("退出登录", status == 200, str(status))
        status, _ = call("POST", "/api/config", {"settings": {}})
        check("退出后拒绝修改", status == 401, str(status))
        status, visitor = call("GET", "/api/metrics")
        check("退出后仍可只读", status == 200 and "metrics" in visitor, str(status))

        status, _ = call("POST", "/api/login", {"username": "admin", "password": "wrong"})
        check("错误密码 401", status == 401, str(status))
        status, _ = call("POST", "/api/login", {"username": "admin", "password": "smoke-test-123"})
        check("重新登录", status == 200, str(status))

        status, page = call("GET", "/")
        check("首页可访问", status == 200 and "SysMon" in str(page))

        print()
        print("失败项:", failures if failures else "无")
        return 1 if failures else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
