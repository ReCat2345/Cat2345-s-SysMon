"""认证：密码哈希、会话、登录限流、初始化令牌。"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

PBKDF2_ITERATIONS = 600_000
COOKIE_NAME = "sysmon_session"
SESSION_TTL = 12 * 3600  # 12 小时


def hash_password(password: str, username: str, iterations: int = PBKDF2_ITERATIONS) -> Dict[str, Any]:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return {
        "username": username,
        "algo": "pbkdf2_sha256",
        "iterations": iterations,
        "salt": salt.hex(),
        "hash": digest.hex(),
        "created_at": int(time.time()),
    }


def verify_password(record: Optional[Dict[str, Any]], username: str, password: str) -> bool:
    if not isinstance(record, dict):
        return False
    try:
        iterations = int(record.get("iterations") or PBKDF2_ITERATIONS)
        salt = bytes.fromhex(record.get("salt") or "")
        expected = bytes.fromhex(record.get("hash") or "")
    except (TypeError, ValueError):
        return False
    if not salt or not expected:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    ok_hash = hmac.compare_digest(digest, expected)
    stored_user = str(record.get("username") or "")
    ok_user = hmac.compare_digest(stored_user.encode("utf-8"), str(username).encode("utf-8"))
    return ok_hash and ok_user


class Sessions:
    """内存会话表；进程重启即失效。"""

    def __init__(self, ttl: int = SESSION_TTL, max_sessions: int = 50):
        self.ttl = ttl
        self.max_sessions = max_sessions
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, Any]] = {}

    def create(self, ip: str, user_agent: str = "") -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._purge(now)
            self._data[token] = {"created": now, "last": now, "ip": ip, "ua": user_agent[:200]}
            if len(self._data) > self.max_sessions:
                oldest = min(self._data, key=lambda k: self._data[k]["created"])
                self._data.pop(oldest, None)
        return token

    def get(self, token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        now = time.time()
        with self._lock:
            sess = self._data.get(token)
            if not sess:
                return None
            if now - sess["created"] > self.ttl:
                self._data.pop(token, None)
                return None
            sess["last"] = now
            return dict(sess, token=token)

    def destroy(self, token: str) -> None:
        with self._lock:
            self._data.pop(token, None)

    def destroy_others(self, keep: str) -> None:
        with self._lock:
            for token in [t for t in self._data if t != keep]:
                self._data.pop(token, None)

    def count(self) -> int:
        with self._lock:
            return len(self._data)

    def _purge(self, now: float) -> None:
        for token in [t for t, s in self._data.items() if now - s["created"] > self.ttl]:
            self._data.pop(token, None)


class Throttle:
    """按 IP 限制登录失败次数：窗口内失败过多则短暂锁定。"""

    def __init__(self, max_failures: int = 5, window: int = 300, lockout: int = 300):
        self.max_failures = max_failures
        self.window = window
        self.lockout = lockout
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, Any]] = {}

    def check(self, ip: str) -> Tuple[bool, int]:
        now = time.time()
        with self._lock:
            rec = self._data.get(ip)
            if not rec:
                return True, 0
            blocked_until = float(rec.get("blocked_until") or 0)
            if blocked_until > now:
                return False, max(1, int(blocked_until - now) + 1)
            fails = [t for t in rec.get("fails", []) if now - t < self.window]
            rec["fails"] = fails
            if len(fails) >= self.max_failures:
                rec["blocked_until"] = now + self.lockout
                return False, self.lockout
            return True, 0

    def failure(self, ip: str) -> None:
        now = time.time()
        with self._lock:
            if len(self._data) > 2000:
                self._data.clear()
            rec = self._data.setdefault(ip, {"fails": [], "blocked_until": 0})
            fails = [t for t in rec.get("fails", []) if now - t < self.window]
            fails.append(now)
            rec["fails"] = fails
            if len(fails) >= self.max_failures:
                rec["blocked_until"] = now + self.lockout
                rec["fails"] = []

    def success(self, ip: str) -> None:
        with self._lock:
            self._data.pop(ip, None)


def ensure_setup_token(path: Path) -> str:
    """首次启动时生成初始化令牌，写入文件并返回。"""
    path = Path(path)
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def remove_setup_token(path: Path) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass
