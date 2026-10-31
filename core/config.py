"""配置模型与持久化。

config.json 结构：
{
  "version": 1,
  "admin": {"username": ..., "algo": "pbkdf2_sha256", "iterations": ..., "salt": ..., "hash": ...},
  "settings": {
      "refresh_seconds": 5,
      "theme": "auto",
      "gpu_enabled": false,
      "branding": {"title": "SysMon", "palette": "violet", "primary": "#6750A4", "wallpaper_opacity": 35},
      "modules": [{"id": "cpu", "visible": true, "chart": "gauge", "hardware": true}, ...]
  }
}
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List

MODULES: List[Dict[str, Any]] = [
    {"id": "cpu", "name": "处理器", "hint": "整体占用", "charts": ["gauge", "wave"], "hardware": True},
    {"id": "memory", "name": "内存", "hint": "物理内存", "charts": ["gauge", "wave"], "hardware": True},
    {"id": "disk", "name": "磁盘", "hint": "分区与读写", "charts": ["gauge", "wave"], "hardware": True},
    {"id": "gpu", "name": "显卡", "hint": "GPU 利用率", "charts": ["gauge", "wave"], "hardware": False},
    {"id": "net", "name": "网络", "hint": "上下行速率", "charts": ["wave"], "hardware": True},
]
MODULE_IDS = [m["id"] for m in MODULES]
MODULE_BY_ID = {m["id"]: m for m in MODULES}

DEFAULT_CHARTS = {"cpu": "gauge", "memory": "wave", "disk": "wave", "gpu": "gauge", "net": "wave"}
DEFAULT_VISIBLE = {"cpu": True, "memory": True, "disk": True, "gpu": True, "net": True}

THEMES = ("auto", "light", "dark")
MIN_REFRESH = 2
MAX_REFRESH = 60
DEFAULT_REFRESH = 5

TITLE_MAX = 40
DEFAULT_TITLE = "SysMon"
DEFAULT_WALLPAPER_OPACITY = 35
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

PALETTES: Dict[str, Dict[str, str]] = {
    "violet": {"name": "紫罗兰", "primary": "#6750a4"},
    "ocean": {"name": "海蓝", "primary": "#00639b"},
    "forest": {"name": "森林", "primary": "#2e6b3e"},
    "sunset": {"name": "日落橙", "primary": "#9a4600"},
    "sakura": {"name": "樱花粉", "primary": "#8e4a5f"},
}
DEFAULT_PALETTE = "violet"
PALETTE_IDS = list(PALETTES.keys()) + ["custom"]


class ConfigError(ValueError):
    """配置校验失败；status 用于 HTTP 状态码。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def default_settings(gpu_supported: bool) -> Dict[str, Any]:
    gpu_on = bool(gpu_supported)
    modules = [
        {
            "id": m["id"],
            "visible": DEFAULT_VISIBLE[m["id"]] and (m["id"] != "gpu" or gpu_on),
            "chart": DEFAULT_CHARTS[m["id"]],
            "hardware": bool(m.get("hardware")),
        }
        for m in MODULES
    ]
    return {
        "refresh_seconds": DEFAULT_REFRESH,
        "theme": "auto",
        "gpu_enabled": gpu_on,
        "minecraft_enabled": False,
        "branding": {
            "title": DEFAULT_TITLE,
            "palette": DEFAULT_PALETTE,
            "primary": PALETTES[DEFAULT_PALETTE]["primary"],
            "wallpaper_opacity": DEFAULT_WALLPAPER_OPACITY,
            "surface_mode": "theme",
            "surface_color": "#f3edf7",
        },
        "modules": modules,
    }


def _as_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} 必须是数字")
    return float(value)


def _normalize_branding(raw: Any, strict: bool) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}

    title_raw = raw.get("title", DEFAULT_TITLE)
    if not isinstance(title_raw, str):
        if strict:
            raise ConfigError("网页标题必须是字符串")
        title_raw = DEFAULT_TITLE
    title = "".join(ch for ch in title_raw if ch.isprintable()).strip()
    if not title:
        title = DEFAULT_TITLE
    if len(title) > TITLE_MAX:
        if strict:
            raise ConfigError(f"网页标题最多 {TITLE_MAX} 个字符")
        title = title[:TITLE_MAX]

    palette = raw.get("palette", DEFAULT_PALETTE)
    if palette not in PALETTE_IDS:
        if strict:
            raise ConfigError("未知的配色方案")
        palette = DEFAULT_PALETTE

    if palette == "custom":
        primary_raw = raw.get("primary", PALETTES[DEFAULT_PALETTE]["primary"])
        if not isinstance(primary_raw, str) or not HEX_RE.match(primary_raw.strip()):
            if strict:
                raise ConfigError("自定义主色需为 #RRGGBB 格式")
            primary = PALETTES[DEFAULT_PALETTE]["primary"]
        else:
            primary = primary_raw.strip().lower()
    else:
        primary = PALETTES[palette]["primary"]

    opacity_raw = raw.get("wallpaper_opacity", DEFAULT_WALLPAPER_OPACITY)
    try:
        opacity = int(round(_as_number(opacity_raw, "壁纸透明度")))
    except ConfigError:
        if strict:
            raise
        opacity = DEFAULT_WALLPAPER_OPACITY
    if not (0 <= opacity <= 100):
        if strict:
            raise ConfigError("壁纸透明度需在 0–100 之间")
        opacity = min(100, max(0, opacity))

    surface_mode = raw.get("surface_mode", "theme")
    if surface_mode not in ("theme", "neutral", "custom"):
        if strict:
            raise ConfigError("卡片配色只能是 theme / neutral / custom")
        surface_mode = "theme"

    surface_color_raw = raw.get("surface_color", "#f3edf7")
    if not isinstance(surface_color_raw, str) or not HEX_RE.match(surface_color_raw.strip()):
        if strict:
            raise ConfigError("卡片颜色需为 #RRGGBB 格式")
        surface_color = "#f3edf7"
    else:
        surface_color = surface_color_raw.strip().lower()

    return {
        "title": title,
        "palette": palette,
        "primary": primary,
        "wallpaper_opacity": opacity,
        "surface_mode": surface_mode,
        "surface_color": surface_color,
    }


def normalize_settings(raw: Any, gpu_supported: bool, strict: bool = True) -> Dict[str, Any]:
    """校验并补全设置。strict=False 时尽量修正而不是报错（用于加载旧配置）。"""
    if not isinstance(raw, dict):
        if strict:
            raise ConfigError("settings 必须是 JSON 对象")
        raw = {}

    refresh = raw.get("refresh_seconds", DEFAULT_REFRESH)
    try:
        refresh = int(round(_as_number(refresh, "刷新间隔")))
    except ConfigError:
        if strict:
            raise
        refresh = DEFAULT_REFRESH
    if not (MIN_REFRESH <= refresh <= MAX_REFRESH):
        if strict:
            raise ConfigError(f"刷新间隔需在 {MIN_REFRESH}–{MAX_REFRESH} 秒之间")
        refresh = min(MAX_REFRESH, max(MIN_REFRESH, refresh))

    theme = raw.get("theme", "auto")
    if theme not in THEMES:
        if strict:
            raise ConfigError("主题只能是 auto / light / dark")
        theme = "auto"

    gpu_enabled = bool(raw.get("gpu_enabled", False))
    if gpu_enabled and not gpu_supported:
        if strict:
            raise ConfigError("当前主机未检测到可用的 GPU，无法启用 GPU 监控", status=409)
        gpu_enabled = False

    branding = _normalize_branding(raw.get("branding"), strict)
    minecraft_enabled = bool(raw.get("minecraft_enabled", False))

    incoming = raw.get("modules", None)
    if incoming is None:
        modules = default_settings(gpu_supported)["modules"]
    else:
        if not isinstance(incoming, list):
            if strict:
                raise ConfigError("modules 必须是数组")
            incoming = []
        picked: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for item in incoming:
            if not isinstance(item, dict):
                if strict:
                    raise ConfigError("modules 中每一项都必须是对象")
                continue
            mid = item.get("id")
            if mid not in MODULE_BY_ID:
                if strict:
                    raise ConfigError(f"未知模块：{mid!r}")
                continue
            if mid in picked:
                if strict:
                    raise ConfigError(f"模块重复：{mid}")
                continue
            charts = MODULE_BY_ID[mid]["charts"]
            chart = item.get("chart", DEFAULT_CHARTS[mid])
            if chart not in charts:
                if strict:
                    raise ConfigError(f"{MODULE_BY_ID[mid]['name']} 不支持 {chart!r} 显示方式")
                chart = charts[0]
            picked[mid] = {
                "id": mid,
                "visible": bool(item.get("visible", True)),
                "chart": chart,
                "hardware": bool(item.get("hardware", MODULE_BY_ID[mid].get("hardware", False))),
            }
            order.append(mid)
        for mid in MODULE_IDS:
            if mid not in picked:
                picked[mid] = {
                    "id": mid,
                    "visible": False,
                    "chart": DEFAULT_CHARTS[mid],
                    "hardware": bool(MODULE_BY_ID[mid].get("hardware", False)),
                }
                order.append(mid)
        modules = [picked[mid] for mid in order]

    if not gpu_enabled:
        for m in modules:
            if m["id"] == "gpu":
                m["visible"] = False
    for m in modules:
        if not MODULE_BY_ID[m["id"]].get("hardware"):
            m["hardware"] = False

    return {
        "refresh_seconds": refresh,
        "theme": theme,
        "gpu_enabled": gpu_enabled,
        "minecraft_enabled": minecraft_enabled,
        "branding": branding,
        "modules": modules,
    }


class Store:
    """线程安全的配置存储，写入时原子替换并收紧权限。"""

    def __init__(self, path: Path, gpu_supported: bool):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._gpu_supported = bool(gpu_supported)
        self._data = self._load()

    # ---------- 读取 ----------
    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "admin": None, "settings": default_settings(self._gpu_supported)}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"配置文件 {self.path} 不是合法 JSON：{exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"配置文件 {self.path} 内容必须是 JSON 对象")
        settings = normalize_settings(raw.get("settings"), self._gpu_supported, strict=False)
        admin = raw.get("admin") if isinstance(raw.get("admin"), dict) else None
        return {"version": 1, "admin": admin, "settings": settings}

    @property
    def configured(self) -> bool:
        with self._lock:
            return bool(self._data.get("admin"))

    @property
    def gpu_supported(self) -> bool:
        return self._gpu_supported

    def settings(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data["settings"])

    def admin(self) -> Dict[str, Any] | None:
        with self._lock:
            admin = self._data.get("admin")
            return copy.deepcopy(admin) if admin else None

    # ---------- 写入 ----------
    def save_settings(self, raw: Any) -> Dict[str, Any]:
        with self._lock:
            settings = normalize_settings(raw, self._gpu_supported, strict=True)
            self._data["settings"] = settings
            self._write()
            return copy.deepcopy(settings)

    def set_admin(self, record: Dict[str, Any]) -> None:
        with self._lock:
            self._data["admin"] = record
            self._write()

    def set_gpu_supported(self, supported: bool) -> None:
        with self._lock:
            self._gpu_supported = bool(supported)
            if not supported:
                settings = self._data["settings"]
                settings["gpu_enabled"] = False
                for m in settings["modules"]:
                    if m["id"] == "gpu":
                        m["visible"] = False
                self._write()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
