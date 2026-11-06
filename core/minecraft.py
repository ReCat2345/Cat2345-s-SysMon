"""Minecraft Java 版服务器监控。

实现 Java Edition Server List Ping（SLP）：VarInt 握手 + status + ping。
同时负责服务器列表、在线玩家历史持久化、玩家头像（正版 UUID -> 皮肤，离线 -> Steve）。
"""

from __future__ import annotations

import base64
import ipaddress
import json
import math
import os
import random
import re
import socket
import struct
import threading
import time
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_PORT = 25565
POLL_INTERVAL = 30
PING_TIMEOUT = 4.0
HISTORY_KEEP = 8 * 24 * 3600
MAX_QUERY_RANGE = 30 * 24 * 3600

MOTD_COLORS: Dict[str, str] = {
    "0": "#000000",
    "1": "#0000AA",
    "2": "#00AA00",
    "3": "#00AAAA",
    "4": "#AA0000",
    "5": "#AA00AA",
    "6": "#FFAA00",
    "7": "#AAAAAA",
    "8": "#555555",
    "9": "#5555FF",
    "a": "#55FF55",
    "b": "#55FFFF",
    "c": "#FF5555",
    "d": "#FF55FF",
    "e": "#FFFF55",
    "f": "#FFFFFF",
}
MOTD_COLOR_NAMES: Dict[str, str] = {
    "black": "#000000",
    "dark_blue": "#0000AA",
    "dark_green": "#00AA00",
    "dark_aqua": "#00AAAA",
    "dark_red": "#AA0000",
    "dark_purple": "#AA00AA",
    "gold": "#FFAA00",
    "gray": "#AAAAAA",
    "dark_gray": "#555555",
    "blue": "#5555FF",
    "green": "#55FF55",
    "aqua": "#55FFFF",
    "red": "#FF5555",
    "light_purple": "#FF55FF",
    "yellow": "#FFFF55",
    "white": "#FFFFFF",
}
MOTD_FORMATS: Dict[str, str] = {
    "k": "obfuscated",
    "l": "bold",
    "m": "strikethrough",
    "n": "underlined",
    "o": "italic",
}
MOTD_STYLE_KEYS = ("bold", "italic", "underlined", "strikethrough", "obfuscated")
MOTD_HEX_RE = re.compile(r"^[0-9A-F]{6}$")
PLAYER_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")

# 1x1 透明 PNG，仅当 static/steve.png 缺失时兜底
FALLBACK_STEVE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


# ------------------------------------------------------------------ VarInt
def _pack_varint(value: int) -> bytes:
    if value < 0:
        value &= 0xFFFFFFFF
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out)


def _decode_varint(data: bytes, index: int) -> Tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if index >= len(data):
            raise ValueError("VarInt 截断")
        byte = data[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, index
        shift += 7
        if shift > 35:
            raise ValueError("VarInt 过长")


def _read_varint(sock: socket.socket) -> int:
    value = 0
    shift = 0
    while True:
        raw = sock.recv(1)
        if not raw:
            raise OSError("连接被关闭")
        byte = raw[0]
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value
        shift += 7
        if shift > 35:
            raise ValueError("VarInt 过长")


def _read_packet(sock: socket.socket) -> bytes:
    length = _read_varint(sock)
    if length <= 0 or length > 2 * 1024 * 1024:
        raise ValueError("数据包长度异常")
    chunks = []
    remaining = length
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise OSError("连接被关闭")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _pack_string(value: str) -> bytes:
    data = value.encode("utf-8")
    return _pack_varint(len(data)) + data


# ------------------------------------------------------------------ 地址
def parse_target(raw: str) -> Tuple[str, int, bool]:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("服务器地址不能为空")
    if "://" in text:
        raise ValueError("只填主机名或 IP，不要带 http:// 之类的前缀")
    host = text
    port = DEFAULT_PORT
    explicit = False
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            raise ValueError("IPv6 地址缺少 ]")
        host = text[1:end]
        rest = text[end + 1 :]
        if rest:
            if not rest.startswith(":"):
                raise ValueError("IPv6 地址格式不正确")
            port = int(rest[1:])
            explicit = True
    elif text.count(":") == 1:
        host, port_text = text.split(":", 1)
        port = int(port_text)
        explicit = True
    if not host or len(host) > 255:
        raise ValueError("主机名长度不合法")
    if any(ch.isspace() for ch in host) or "/" in host or "@" in host:
        raise ValueError("主机名包含非法字符")
    if not (1 <= int(port) <= 65535):
        raise ValueError("端口需在 1–65535 之间")
    return host, int(port), explicit


def parse_address(raw: str) -> Tuple[str, int]:
    host, port, _ = parse_target(raw)
    return host, port


# ------------------------------------------------------------------ SRV
SRV_CACHE: Dict[str, Tuple[float, Optional[Tuple[str, int]]]] = {}
SRV_CACHE_LOCK = threading.Lock()
SRV_CACHE_TTL = 300


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _encode_dns_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        data = label.encode("idna")
        if not data or len(data) > 63:
            raise ValueError("DNS 名称不合法")
        out += bytes([len(data)]) + data
    return out + b"\x00"


def _decode_dns_name(data: bytes, offset: int) -> Tuple[str, int]:
    labels: List[str] = []
    jumped = False
    next_offset = offset
    seen = set()
    for _ in range(128):
        if offset >= len(data):
            raise ValueError("DNS 名称截断")
        length = data[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise ValueError("DNS 指针截断")
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if not jumped:
                next_offset = offset + 2
            jumped = True
            if pointer in seen:
                raise ValueError("DNS 指针循环")
            seen.add(pointer)
            offset = pointer
            continue
        if length == 0:
            offset += 1
            if not jumped:
                next_offset = offset
            break
        labels.append(data[offset + 1 : offset + 1 + length].decode("utf-8", "replace"))
        offset += 1 + length
    return ".".join(labels), next_offset


def _parse_srv_response(data: bytes, query_id: int) -> List[Tuple[int, int, int, str]]:
    if len(data) < 12:
        return []
    rid, _flags, qdcount, ancount, _ns, _ar = struct.unpack(">HHHHHH", data[:12])
    if rid != query_id:
        return []
    offset = 12
    for _ in range(qdcount):
        _, offset = _decode_dns_name(data, offset)
        offset += 4
    records: List[Tuple[int, int, int, str]] = []
    for _ in range(ancount):
        try:
            _, offset = _decode_dns_name(data, offset)
            if offset + 10 > len(data):
                break
            rtype, rclass, _ttl, rdlen = struct.unpack(">HHIH", data[offset : offset + 10])
            offset += 10
            if rtype == 33 and rclass == 1 and rdlen >= 7:
                priority, weight, port = struct.unpack(">HHH", data[offset : offset + 6])
                target, _ = _decode_dns_name(data, offset + 6)
                records.append((priority, weight, port, target.rstrip(".")))
            offset += rdlen
        except (ValueError, struct.error):
            break
    return records


def _dns_query_srv(name: str, nameserver: str, timeout: float = 1.5) -> List[Tuple[int, int, int, str]]:
    query_id = random.randint(0, 0xFFFF)
    header = struct.pack(">HHHHHH", query_id, 0x0100, 1, 0, 0, 0)
    question = _encode_dns_name(name) + struct.pack(">HH", 33, 1)
    packet = header + question
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(packet, (nameserver, 53))
            data, _ = sock.recvfrom(4096)
    except OSError:
        return []
    return _parse_srv_response(data, query_id)


def _system_nameservers() -> List[str]:
    servers: List[str] = []

    def add(value: str) -> None:
        for part in re.split(r"[\s,;]+", value or ""):
            part = part.strip()
            if not part:
                continue
            try:
                socket.inet_aton(part)
            except OSError:
                continue
            if part not in servers:
                servers.append(part)

    try:
        with open("/etc/resolv.conf", "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.strip().startswith("nameserver"):
                    add(line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else "")
    except OSError:
        pass

    if os.name == "nt" and not servers:
        try:
            import winreg

            paths = [
                (r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters", "NameServer"),
                (r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters", "DhcpNameServer"),
            ]
            for path, value_name in paths:
                try:
                    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
                    value, _ = winreg.QueryValueEx(key, value_name)
                    add(str(value))
                except OSError:
                    continue
            try:
                interfaces = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces",
                )
                index = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(interfaces, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        sub = winreg.OpenKey(interfaces, sub_name)
                        for value_name in ("NameServer", "DhcpNameServer"):
                            try:
                                value, _ = winreg.QueryValueEx(sub, value_name)
                                add(str(value))
                            except OSError:
                                continue
                    except OSError:
                        continue
            except OSError:
                pass
        except Exception:
            pass

    if not servers:
        servers = ["8.8.8.8", "1.1.1.1"]
    return servers


def resolve_srv(host: str) -> Optional[Tuple[str, int]]:
    key = host.rstrip(".").lower()
    now = time.time()
    with SRV_CACHE_LOCK:
        cached = SRV_CACHE.get(key)
    if cached and now - cached[0] < SRV_CACHE_TTL:
        return cached[1]
    result: Optional[Tuple[str, int]] = None
    query_name = f"_minecraft._tcp.{key}"
    for nameserver in _system_nameservers():
        records = _dns_query_srv(query_name, nameserver)
        if not records:
            continue
        records.sort(key=lambda item: (item[0], -item[1]))
        _priority, _weight, port, target = records[0]
        if target and target != ".":
            result = (target, int(port))
        break
    with SRV_CACHE_LOCK:
        if len(SRV_CACHE) > 500:
            SRV_CACHE.clear()
        SRV_CACHE[key] = (now, result)
    return result



def format_address(host: str, port: int) -> str:
    if ":" in host:
        return f"[{host}]:{port}"
    return f"{host}:{port}" if port != DEFAULT_PORT else host


# ------------------------------------------------------------------ MOTD 格式化
def _motd_style(color: Optional[str] = None) -> Dict[str, Any]:
    style: Dict[str, Any] = {"color": color}
    for key in MOTD_STYLE_KEYS:
        style[key] = False
    return style


def _motd_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def _motd_color(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith("#") and MOTD_HEX_RE.match(text[1:].upper()):
        return "#" + text[1:].upper()
    return MOTD_COLOR_NAMES.get(text.lower())


def _motd_color_code(color: str) -> str:
    for code, hex_value in MOTD_COLORS.items():
        if hex_value == color.upper():
            return f"§{code}"
    return f"§#{color.lstrip('#').upper()}"


def _motd_style_prefix(style: Dict[str, Any]) -> str:
    parts = ["§r"]
    color = style.get("color")
    if color:
        parts.append(_motd_color_code(str(color)))
    for key, code in (
        ("obfuscated", "k"),
        ("bold", "l"),
        ("strikethrough", "m"),
        ("underlined", "n"),
        ("italic", "o"),
    ):
        if style.get(key):
            parts.append(f"§{code}")
    return "".join(parts)


def _component_to_legacy(node: Any, inherited: Dict[str, Any]) -> str:
    """把 JSON 文本组件转换成带 § 代码的字符串，便于统一解析。"""
    if isinstance(node, str):
        return _motd_style_prefix(inherited) + node
    if isinstance(node, list):
        return "".join(_component_to_legacy(item, inherited) for item in node)
    if not isinstance(node, dict):
        return ""
    style = dict(inherited)
    if "color" in node:
        color = _motd_color(node.get("color"))
        raw_color = str(node.get("color") or "").strip().lower()
        if color:
            style["color"] = color
        elif raw_color == "reset":
            style["color"] = None
    for key in MOTD_STYLE_KEYS:
        if key in node:
            style[key] = _motd_bool(node.get(key))
    text = node.get("text")
    text = text if isinstance(text, str) else ""
    out = _motd_style_prefix(style) + text
    extra = node.get("extra")
    if isinstance(extra, list):
        for item in extra:
            out += _component_to_legacy(item, style)
    elif extra is not None:
        out += _component_to_legacy(extra, style)
    return out


def _to_legacy_motd(description: Any) -> str:
    if isinstance(description, str):
        return description
    return _component_to_legacy(description, _motd_style())


def _parse_motd_codes(text: str) -> List[Dict[str, Any]]:
    """解析 § / & 格式代码，输出带样式和 HEX 颜色的片段。"""
    segments: List[Dict[str, Any]] = []
    buffer: List[str] = []
    style = _motd_style()

    def flush() -> None:
        if not buffer:
            return
        chunk = "".join(buffer)
        buffer.clear()
        if chunk:
            segments.append(
                {
                    "text": chunk,
                    "color": style.get("color"),
                    **{key: bool(style.get(key)) for key in MOTD_STYLE_KEYS},
                }
            )

    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        if char not in ("§", "&"):
            buffer.append(char)
            i += 1
            continue

        # && / §§ 作为转义后的字面符号
        if i + 1 < length and text[i + 1] == char:
            buffer.append(char)
            i += 2
            continue

        # &#RRGGBB 或 §#RRGGBB
        if i + 1 < length and text[i + 1] == "#":
            digits = text[i + 2 : i + 8]
            if len(digits) == 6 and MOTD_HEX_RE.match(digits.upper()):
                flush()
                style = _motd_style("#" + digits.upper())
                i += 8
                continue

        if i + 1 >= length:
            buffer.append(char)
            i += 1
            continue

        code = text[i + 1].lower()

        # §x§R§R§G§G§B§B 形式的原版 HEX
        if code == "x":
            j = i + 2
            digits: List[str] = []
            valid = True
            for _ in range(6):
                if j + 1 >= length or text[j] != char:
                    valid = False
                    break
                digit = text[j + 1].lower()
                if digit not in "0123456789abcdef":
                    valid = False
                    break
                digits.append(digit)
                j += 2
            if valid:
                flush()
                style = _motd_style("#" + "".join(digits).upper())
                i = j
                continue

        if code in MOTD_COLORS:
            # Java 版规则：颜色代码会结束之前的格式，格式代码要放在颜色之后
            flush()
            style = _motd_style(MOTD_COLORS[code])
            i += 2
            continue

        if code in MOTD_FORMATS:
            flush()
            style[MOTD_FORMATS[code]] = True
            i += 2
            continue

        if code == "r":
            flush()
            style = _motd_style()
            i += 2
            continue

        # 不是有效代码：保留前缀字符，不吞掉后面的普通文本
        buffer.append(char)
        i += 1

    flush()
    return segments


def _parse_motd(description: Any) -> Tuple[str, List[Dict[str, Any]]]:
    segments = _parse_motd_codes(_to_legacy_motd(description))
    text = "".join(segment["text"] for segment in segments)
    plain = " ".join(text.replace("\r", "\n").split())
    return plain, segments


def _flatten_motd(description: Any) -> str:
    plain, _segments = _parse_motd(description)
    return plain


def ping_server(host: str, port: int = DEFAULT_PORT, timeout: float = PING_TIMEOUT) -> Dict[str, Any]:
    started = time.perf_counter()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)

        handshake = (
            _pack_varint(0x00)
            + _pack_varint(760)
            + _pack_string(host)
            + struct.pack(">H", port)
            + _pack_varint(1)
        )
        sock.sendall(_pack_varint(len(handshake)) + handshake)
        sock.sendall(_pack_varint(1) + _pack_varint(0x00))

        packet = _read_packet(sock)
        status_latency = max(1, int((time.perf_counter() - started) * 1000))

        packet_id, index = _decode_varint(packet, 0)
        if packet_id != 0x00:
            raise ValueError("status 响应格式不正确")
        length, index = _decode_varint(packet, index)
        if index + length > len(packet):
            raise ValueError("status JSON 长度异常")
        payload = json.loads(packet[index : index + length].decode("utf-8"))

        latency = status_latency
        try:
            ping_started = time.perf_counter()
            ping_payload = _pack_varint(0x01) + struct.pack(">q", int(time.time() * 1000))
            sock.sendall(_pack_varint(len(ping_payload)) + ping_payload)
            _read_packet(sock)
            latency = max(1, int((time.perf_counter() - ping_started) * 1000))
        except (OSError, ValueError):
            pass

    version = payload.get("version") or {}
    players = payload.get("players") or {}
    motd, motd_segments = _parse_motd(payload.get("description"))
    sample = []
    for item in players.get("sample") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            sample.append({"name": name, "id": item.get("id") or ""})
    return {
        "online": True,
        "latency_ms": latency,
        "version": str(version.get("name") or ""),
        "protocol": int(version.get("protocol") or 0) or None,
        "players": {
            "online": int(players.get("online") or 0),
            "max": int(players.get("max") or 0),
            "sample": sample,
        },
        "motd": motd,
        "motd_segments": motd_segments,
        "error": "",
        "checked_at": int(time.time()),
    }


# ------------------------------------------------------------------ 监控
class MinecraftMonitor:
    def __init__(self, data_dir: Path, config_store: Any):
        self.data_dir = Path(data_dir)
        self.config_store = config_store
        self.servers_path = self.data_dir / "mc_servers.json"
        self.history_path = self.data_dir / "mc_history.json"
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._servers: List[Dict[str, Any]] = self._load_servers()
        self._history: Dict[str, List[List[int]]] = self._load_history()
        self._status: Dict[str, Dict[str, Any]] = {}
        self._avatar_cache: Dict[str, Tuple[float, bytes, str]] = {}
        self._steve: Optional[bytes] = None

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="mc-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.enabled():
                try:
                    self.refresh_all()
                except Exception:
                    pass
            self._wake.wait(POLL_INTERVAL)
            self._wake.clear()

    def enabled(self) -> bool:
        try:
            return bool(self.config_store.settings().get("minecraft_enabled"))
        except Exception:
            return False

    # ---------- 文件 ----------
    def _load_servers(self) -> List[Dict[str, Any]]:
        try:
            raw = json.loads(self.servers_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        items = raw.get("servers") if isinstance(raw, dict) else None
        servers: List[Dict[str, Any]] = []
        if not isinstance(items, list):
            return servers
        for item in items:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("id") or "")
            name = str(item.get("name") or "").strip()
            address = str(item.get("address") or "").strip()
            if not sid or not name or not address:
                continue
            servers.append(
                {
                    "id": sid,
                    "name": name[:40],
                    "address": address,
                    "created_at": int(item.get("created_at") or 0),
                }
            )
        return servers

    def _save_servers(self) -> None:
        payload = {"version": 1, "servers": self._servers}
        tmp = self.servers_path.with_name(self.servers_path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.servers_path)

    def _load_history(self) -> Dict[str, List[List[int]]]:
        try:
            raw = json.loads(self.history_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        items = raw.get("servers") if isinstance(raw, dict) else None
        if not isinstance(items, dict):
            return {}
        cutoff = int(time.time()) - HISTORY_KEEP
        history: Dict[str, List[List[int]]] = {}
        for sid, points in items.items():
            if not isinstance(points, list):
                continue
            cleaned: List[List[int]] = []
            for point in points:
                if not isinstance(point, list) or len(point) < 2:
                    continue
                try:
                    ts = int(point[0])
                    value = max(0, int(point[1]))
                except (TypeError, ValueError):
                    continue
                if ts >= cutoff:
                    cleaned.append([ts, value])
            cleaned.sort(key=lambda item: item[0])
            if cleaned:
                history[str(sid)] = cleaned
        return history

    def _save_history(self) -> None:
        with self._lock:
            cutoff = int(time.time()) - HISTORY_KEEP
            payload = {
                "version": 1,
                "servers": {
                    sid: [point for point in points if point[0] >= cutoff]
                    for sid, points in self._history.items()
                },
            }
        tmp = self.history_path.with_name(self.history_path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.history_path)

    # ---------- 服务器管理 ----------
    def list_servers(self) -> List[Dict[str, Any]]:
        with self._lock:
            servers = list(self._servers)
        return [self._public_server(item) for item in servers]

    def get_server(self, server_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            item = next((s for s in self._servers if s["id"] == server_id), None)
        return self._public_server(item) if item else None

    def add_server(self, name: str, address: str) -> Dict[str, Any]:
        clean_name = " ".join(str(name or "").split())
        if not clean_name:
            raise ValueError("服务器名称不能为空")
        if len(clean_name) > 40:
            raise ValueError("服务器名称最多 40 个字符")
        host, port = parse_address(address)
        display = format_address(host, port)
        with self._lock:
            if any(item["address"].lower() == display.lower() for item in self._servers):
                raise ValueError("这个地址已经添加过了")
            item = {
                "id": uuid.uuid4().hex[:12],
                "name": clean_name,
                "address": display,
                "created_at": int(time.time()),
            }
            self._servers.append(item)
            self._save_servers()
        self._wake.set()
        threading.Thread(target=self.refresh_all, name="mc-initial-refresh", daemon=True).start()
        return self._public_server(item)

    def update_server(self, server_id: str, name: str, address: str) -> Dict[str, Any]:
        clean_name = " ".join(str(name or "").split())
        if not clean_name:
            raise ValueError("服务器名称不能为空")
        if len(clean_name) > 40:
            raise ValueError("服务器名称最多 40 个字符")
        host, port = parse_address(address)
        display = format_address(host, port)
        with self._lock:
            item = next((s for s in self._servers if s["id"] == server_id), None)
            if not item:
                raise ValueError("服务器不存在")
            if any(
                other["id"] != server_id and other["address"].lower() == display.lower()
                for other in self._servers
            ):
                raise ValueError("这个地址已经添加过了")
            address_changed = item["address"].lower() != display.lower()
            item["name"] = clean_name
            item["address"] = display
            if address_changed:
                self._status.pop(server_id, None)
            self._save_servers()
        self._wake.set()
        threading.Thread(target=self.refresh_all, name="mc-edit-refresh", daemon=True).start()
        return self._public_server(item)

    def _public_server(self, item: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            status = dict(self._status.get(item["id"]) or {})
        base: Dict[str, Any] = {
            "online": False,
            "latency_ms": None,
            "version": "",
            "protocol": None,
            "players": {"online": 0, "max": 0, "sample": []},
            "motd": "",
            "motd_segments": [],
            "checked_at": 0,
            "error": "尚未检测",
        }
        base.update(status)
        base.update({"id": item["id"], "name": item["name"], "address": item["address"]})
        return base

    # ---------- 探测 ----------
    def refresh_all(self) -> None:
        with self._lock:
            servers = list(self._servers)
        if not servers:
            return

        def probe(item: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            srv_info = None
            try:
                host, port, explicit = parse_target(item["address"])
                if not explicit and not _is_ip(host):
                    resolved = resolve_srv(host)
                    if resolved:
                        host, port = resolved
                        srv_info = {"target": host, "port": port}
                status = ping_server(host, port)
                if srv_info:
                    status["srv"] = srv_info
                return item, status
            except Exception as exc:
                return item, {
                    "online": False,
                    "latency_ms": None,
                    "version": "",
                    "protocol": None,
                    "players": {"online": 0, "max": 0, "sample": []},
                    "motd": "",
                    "error": str(exc)[:160] or "连接失败",
                    "checked_at": int(time.time()),
                    "srv": srv_info,
                }

        results: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        with ThreadPoolExecutor(max_workers=min(6, len(servers))) as pool:
            futures = [pool.submit(probe, item) for item in servers]
            for future in futures:
                try:
                    results.append(future.result(timeout=PING_TIMEOUT + 4))
                except Exception:
                    continue

        with self._lock:
            for item, status in results:
                self._status[item["id"]] = status
        for item, status in results:
            players = status.get("players") or {}
            self._record(item["id"], int(players.get("online") or 0))
        if results:
            self._save_history()

    def _record(self, server_id: str, online: int) -> None:
        minute = int(time.time() // 60 * 60)
        with self._lock:
            points = self._history.setdefault(server_id, [])
            if points and points[-1][0] == minute:
                points[-1][1] = max(points[-1][1], online)
            elif not points or points[-1][0] < minute:
                points.append([minute, online])
            cutoff = int(time.time()) - HISTORY_KEEP
            self._history[server_id] = [point for point in points if point[0] >= cutoff]

    # ---------- 历史 ----------
    def history(self, server_id: str, from_ts: int, to_ts: int) -> Dict[str, Any]:
        now = int(time.time())
        to_ts = min(int(to_ts), now + 60)
        from_ts = int(from_ts)
        if to_ts <= from_ts:
            raise ValueError("结束时间必须晚于开始时间")
        if from_ts < to_ts - MAX_QUERY_RANGE:
            from_ts = to_ts - MAX_QUERY_RANGE

        duration = to_ts - from_ts
        if duration <= 3600:
            bucket = 60
        elif duration <= 6 * 3600:
            bucket = 300
        elif duration <= 12 * 3600:
            bucket = 600
        elif duration <= 24 * 3600:
            bucket = 900
        elif duration <= 7 * 86400:
            bucket = 3600
        else:
            bucket = max(3600, int(math.ceil(duration / 240 / 3600)) * 3600)

        with self._lock:
            points = list(self._history.get(server_id) or [])

        buckets: Dict[int, int] = {}
        for ts, value in points:
            if ts < from_ts or ts > to_ts:
                continue
            key = max(from_ts, int(ts // bucket) * bucket)
            buckets[key] = max(buckets.get(key, 0), value)

        series = [[key, buckets[key]] for key in sorted(buckets)]
        return {
            "from_ts": from_ts,
            "to_ts": to_ts,
            "bucket_seconds": bucket,
            "peak": max((item[1] for item in series), default=0),
            "points": series,
        }

    # ---------- 头像 ----------
    def _steve_bytes(self) -> bytes:
        if self._steve is None:
            path = Path(__file__).resolve().parents[1] / "static" / "steve.png"
            try:
                self._steve = path.read_bytes()
            except OSError:
                self._steve = FALLBACK_STEVE
        return self._steve

    def avatar(self, name: str) -> Tuple[bytes, str]:
        if not PLAYER_NAME_RE.match(str(name or "")):
            raise ValueError("玩家名不合法")
        if not self.enabled():
            raise ValueError("MC 监控未启用")
        key = name.lower()
        now = time.time()
        with self._lock:
            cached = self._avatar_cache.get(key)
        if cached and now - cached[0] < 3600:
            return cached[1], cached[2]

        uuid_value = None
        try:
            request = urllib.request.Request(
                "https://api.mojang.com/users/profiles/minecraft/" + urllib.parse.quote(name),
                headers={"User-Agent": "SysMon/1.5.0"},
            )
            with urllib.request.urlopen(request, timeout=4) as response:
                payload = json.loads(response.read().decode("utf-8"))
            uuid_value = str(payload.get("id") or "")
            if not re.fullmatch(r"[0-9a-fA-F]{32}", uuid_value):
                uuid_value = None
        except Exception:
            uuid_value = None

        if uuid_value:
            try:
                request = urllib.request.Request(
                    f"https://mc-heads.net/avatar/{uuid_value}/64",
                    headers={"User-Agent": "SysMon/1.5.0"},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    image = response.read()
                if image.startswith(b"\x89PNG\r\n\x1a\n"):
                    result = (image, "image/png")
                else:
                    result = (self._steve_bytes(), "image/png")
            except Exception:
                result = (self._steve_bytes(), "image/png")
        else:
            result = (self._steve_bytes(), "image/png")

        with self._lock:
            if len(self._avatar_cache) > 500:
                self._avatar_cache.clear()
            self._avatar_cache[key] = (now, result[0], result[1])
        return result
