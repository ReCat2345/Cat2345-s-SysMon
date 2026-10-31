"""品牌资源存储：网页图标与壁纸。

文件保存在 data/branding/ 下，元信息（MIME、更新时间）保存在 data/branding.json。
只接受经过魔数校验的 PNG / JPEG / WebP（图标额外允许 ICO），避免把 SVG 当同源脚本执行。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ICON_MAX = 512 * 1024
WALLPAPER_MAX = 8 * 1024 * 1024
KINDS = ("icon", "wallpaper")


def sniff_image(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    return None


def limit_for(kind: str) -> int:
    return ICON_MAX if kind == "icon" else WALLPAPER_MAX


class BrandingStore:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir)
        self.dir = self.root / "branding"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.root / "branding.json"
        self._lock = threading.Lock()
        self._meta: Dict[str, Dict[str, Any]] = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        try:
            raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {k: v for k, v in raw.items() if k in KINDS and isinstance(v, dict)}

    def assets(self) -> Dict[str, Optional[str]]:
        with self._lock:
            result: Dict[str, Optional[str]] = {}
            for kind in KINDS:
                info = self._meta.get(kind)
                if info and (self.dir / kind).is_file():
                    result[kind] = f"/branding/{kind}?v={int(info.get('updated', 0))}"
                else:
                    result[kind] = None
            return result

    def get(self, kind: str) -> Optional[Tuple[Path, str]]:
        if kind not in KINDS:
            return None
        with self._lock:
            info = self._meta.get(kind)
            path = self.dir / kind
            if not info or not path.is_file():
                return None
            return path, str(info.get("mime") or "application/octet-stream")

    def save(self, kind: str, data: bytes, mime: str) -> None:
        if kind not in KINDS:
            raise ValueError("unknown asset kind")
        with self._lock:
            path = self.dir / kind
            tmp = self.dir / f"{kind}.tmp"
            tmp.write_bytes(data)
            os.replace(tmp, path)
            self._meta[kind] = {
                "mime": mime,
                "size": len(data),
                "updated": int(time.time() * 1000),
            }
            self._write()

    def remove(self, kind: str) -> None:
        if kind not in KINDS:
            return
        with self._lock:
            self._meta.pop(kind, None)
            try:
                (self.dir / kind).unlink()
            except OSError:
                pass
            self._write()

    def _write(self) -> None:
        tmp = self.root / "branding.json.tmp"
        tmp.write_text(json.dumps(self._meta, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.meta_path)
