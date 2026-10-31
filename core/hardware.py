"""硬件信息采集：CPU 型号、内存条、磁盘型号、网卡型号。

启动后由后台线程采集一次（约 1–10 秒，取决于平台），不阻塞 Web 服务。
拿不到细节时退化为总数/型号名，保证界面始终有内容。
"""

from __future__ import annotations

import copy
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

MEMORY_TYPES = {
    20: "DDR",
    21: "DDR2",
    22: "DDR2",
    24: "DDR3",
    26: "DDR4",
    27: "LPDDR",
    28: "LPDDR2",
    29: "LPDDR3",
    30: "LPDDR4",
    34: "DDR5",
    35: "LPDDR5",
}

PCI_VENDORS = {
    "0x8086": "Intel",
    "0x10ec": "Realtek",
    "0x14e4": "Broadcom",
    "0x15b3": "Mellanox",
    "0x1af4": "Virtio",
    "0x1022": "AMD",
}

VIRTUAL_NIC_HINTS = (
    "vmware",
    "virtualbox",
    "hyper-v",
    "vethernet",
    "vpn",
    "tap-",
    "tun",
    "loopback",
    "docker",
    "veth",
    "br-",
    "bridge",
    "wsl",
    "bluetooth",
    "wan miniport",
)


def _looks_virtual_nic(nic: Dict[str, Any]) -> bool:
    text = f"{nic.get('name') or ''} {nic.get('interface') or ''}".lower()
    return any(hint in text for hint in VIRTUAL_NIC_HINTS)


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _read_int(path: Path) -> Optional[int]:
    raw = _read_text(path)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def cpu_model() -> str:
    try:
        if os.path.exists("/proc/cpuinfo"):
            with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        if os.name == "nt":
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            )
            value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            return str(value).strip()
    except Exception:
        pass
    return platform.processor() or ""


def host_info() -> Dict[str, Any]:
    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = "unknown"
    return {
        "hostname": hostname,
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "cpu_model": cpu_model(),
        "cores": psutil.cpu_count() or 0,
        "boot": psutil.boot_time(),
    }


def _run(cmd: List[str], timeout: float = 8.0) -> Optional[str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _powershell_json(script: str) -> Any:
    if os.name != "nt":
        return None
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        candidate = (
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        if candidate.is_file():
            exe = str(candidate)
    if not exe:
        return None
    out = _run([exe, "-NoProfile", "-NonInteractive", "-Command", script], timeout=10.0)
    if not out:
        return None
    raw = out.strip().lstrip("\ufeff")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _as_list(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _size_to_gb(text: str) -> Optional[float]:
    match = re.search(r"([\d.]+)\s*(GB|GiB|MB|MiB|TB|TiB)", text or "", re.I)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).upper()
    if unit in ("MB", "MIB"):
        return value / 1024
    if unit in ("TB", "TIB"):
        return value * 1024
    return value


def _speed_mbps(text: Any) -> Optional[float]:
    if isinstance(text, (int, float)):
        return float(text)
    match = re.search(r"([\d.]+)\s*(Gbps|Mbps|Kbps)", str(text or ""), re.I)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit == "gbps":
        return value * 1000
    if unit == "kbps":
        return value / 1000
    return value


def _memory_summary(modules: List[Dict[str, Any]], total_gb: float) -> str:
    if not modules:
        return f"总容量 {total_gb:g} GiB"
    types = sorted({m["type"] for m in modules if m.get("type")})
    speeds = sorted({m["speed"] for m in modules if m.get("speed")})
    capacities = [m.get("capacity_gb") or 0 for m in modules]
    if capacities and len(set(capacities)) == 1:
        capacity_text = f"{len(capacities)} × {capacities[0]:g} GiB"
    else:
        capacity_text = f"共 {total_gb:g} GiB"
    parts = []
    if types:
        parts.append("/".join(types))
    if speeds:
        parts.append(f"{int(max(speeds))} MT/s")
    parts.append(capacity_text)
    return " · ".join(parts)


class HardwareInfo:
    """后台采集并缓存硬件信息。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._data: Dict[str, Any] = self._basic()

    def _basic(self) -> Dict[str, Any]:
        memory = psutil.virtual_memory()
        total_gb = round(memory.total / 1024 ** 3, 1)
        freq = None
        try:
            raw = psutil.cpu_freq()
            if raw and raw.max:
                freq = round(raw.max / 1000, 2)
        except Exception:
            pass
        return {
            "ready": False,
            "cpu": {
                "model": cpu_model(),
                "physical": psutil.cpu_count(logical=False),
                "logical": psutil.cpu_count(logical=True),
                "max_ghz": freq,
            },
            "memory": {
                "total_gb": total_gb,
                "summary": f"总容量 {total_gb:g} GiB",
                "modules": [],
            },
            "disks": [],
            "nics": [],
            "updated_at": 0,
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._collect, name="hardware-info", daemon=True)
        self._thread.start()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    # ------------------------------------------------------------------ 采集
    def _collect(self) -> None:
        for key, func in (("memory", self._memory), ("disks", self._disks), ("nics", self._nics)):
            try:
                value = func()
            except Exception:
                value = None
            if not value:
                continue
            with self._lock:
                self._data[key] = value
        with self._lock:
            self._data["ready"] = True
            self._data["updated_at"] = int(time.time())

    def _memory(self) -> Optional[Dict[str, Any]]:
        total_gb = round(psutil.virtual_memory().total / 1024 ** 3, 1)
        modules: List[Dict[str, Any]] = []
        if os.name == "nt":
            data = _powershell_json(
                "Get-CimInstance Win32_PhysicalMemory | "
                "Select-Object Manufacturer,PartNumber,Capacity,Speed,MemoryType,SMBIOSMemoryType | "
                "ConvertTo-Json -Compress"
            )
            for row in _as_list(data):
                capacity = row.get("Capacity")
                if not isinstance(capacity, (int, float)) or capacity <= 0:
                    continue
                mem_type = ""
                for key in ("SMBIOSMemoryType", "MemoryType"):
                    code = row.get(key)
                    if isinstance(code, int) and code in MEMORY_TYPES:
                        mem_type = MEMORY_TYPES[code]
                        break
                speed = row.get("Speed")
                modules.append(
                    {
                        "capacity_gb": round(float(capacity) / 1024 ** 3, 1),
                        "type": mem_type,
                        "speed": int(speed) if isinstance(speed, (int, float)) and speed else None,
                        "label": f"{round(float(capacity) / 1024 ** 3, 1):g} GiB {mem_type}".strip(),
                    }
                )
        elif sys_platform_is_linux():
            modules = self._linux_memory_modules()
        elif platform.system() == "Darwin":
            modules = self._mac_memory_modules()

        summary = _memory_summary(modules, total_gb)
        return {"total_gb": total_gb, "summary": summary, "modules": modules}

    def _linux_memory_modules(self) -> List[Dict[str, Any]]:
        tool = shutil.which("dmidecode")
        if not tool:
            return []
        out = _run([tool, "-t", "memory"], timeout=6.0)
        if not out:
            return []
        modules: List[Dict[str, Any]] = []
        for block in out.split("\n\n"):
            if "Memory Device" not in block:
                continue
            size = re.search(r"^\s*Size:\s*(.+)$", block, re.M)
            if not size or "No Module" in size.group(1):
                continue
            size_gb = _size_to_gb(size.group(1))
            if not size_gb:
                continue
            type_match = re.search(r"^\s*Type:\s*(.+)$", block, re.M)
            speed_match = re.search(r"^\s*Speed:\s*(\d+)", block, re.M)
            manufacturer = re.search(r"^\s*Manufacturer:\s*(.+)$", block, re.M)
            part = re.search(r"^\s*Part Number:\s*(.+)$", block, re.M)
            mem_type = type_match.group(1).strip() if type_match else ""
            speed = int(speed_match.group(1)) if speed_match else None
            label = f"{size_gb:g} GiB {mem_type}".strip()
            detail = " ".join(
                item.strip()
                for item in (manufacturer.group(1) if manufacturer else "", part.group(1) if part else "")
                if item and item.strip()
            )
            if detail:
                label = f"{label} · {detail}"
            modules.append(
                {"capacity_gb": size_gb, "type": mem_type, "speed": speed, "label": label}
            )
        return modules

    def _mac_memory_modules(self) -> List[Dict[str, Any]]:
        out = _run(["system_profiler", "SPMemoryDataType", "-json"], timeout=8.0)
        if not out:
            return []
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return []
        modules: List[Dict[str, Any]] = []
        for item in data.get("SPMemoryDataType", []):
            for dimm in item.get("_items", []):
                size_gb = _size_to_gb(str(dimm.get("dimm_size") or ""))
                if not size_gb:
                    continue
                modules.append(
                    {
                        "capacity_gb": size_gb,
                        "type": str(dimm.get("dimm_type") or ""),
                        "speed": None,
                        "label": f"{size_gb:g} GiB {dimm.get('dimm_type') or ''}".strip(),
                    }
                )
        return modules

    def _disks(self) -> List[Dict[str, Any]]:
        disks: List[Dict[str, Any]] = []
        if os.name == "nt":
            data = _powershell_json(
                "Get-PhysicalDisk | Select-Object FriendlyName,MediaType,Size | ConvertTo-Json -Compress"
            )
            rows = _as_list(data)
            if not rows:
                data = _powershell_json(
                    "Get-CimInstance Win32_DiskDrive | Select-Object Model,Size,MediaType | "
                    "ConvertTo-Json -Compress"
                )
                rows = [
                    {"FriendlyName": r.get("Model"), "Size": r.get("Size"), "MediaType": r.get("MediaType")}
                    for r in _as_list(data)
                ]
            for row in rows:
                name = str(row.get("FriendlyName") or "").strip()
                size = row.get("Size")
                media = str(row.get("MediaType") or "").strip()
                lowered = media.lower()
                if "ssd" in lowered or media == "4":
                    kind = "SSD"
                elif "hdd" in lowered or media == "3":
                    kind = "HDD"
                else:
                    kind = ""
                if not name:
                    continue
                disks.append(
                    {
                        "name": name,
                        "size_gb": round(float(size) / 1024 ** 3, 1) if isinstance(size, (int, float)) else None,
                        "kind": kind,
                    }
                )
        elif sys_platform_is_linux():
            for block in sorted(Path("/sys/block").glob("*")):
                name = block.name
                if name.startswith(("loop", "ram", "dm-", "md", "sr", "fd", "zram")):
                    continue
                model = _read_text(block / "device/model") or _read_text(block / "device/name") or name
                vendor = _read_text(block / "device/vendor") or ""
                sectors = _read_int(block / "size")
                rotational = _read_text(block / "queue/rotational")
                kind = "SSD" if rotational == "0" else "HDD" if rotational == "1" else ""
                label = f"{vendor} {model}".strip()
                disks.append(
                    {
                        "name": label,
                        "size_gb": round(sectors * 512 / 1024 ** 3, 1) if sectors else None,
                        "kind": kind,
                    }
                )
        if not disks:
            # 退化：用分区挂载点凑一条信息
            try:
                parts = psutil.disk_partitions(all=False)
            except Exception:
                parts = []
            for part in parts[:3]:
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                except OSError:
                    continue
                disks.append(
                    {
                        "name": part.device,
                        "size_gb": round(usage.total / 1024 ** 3, 1),
                        "kind": "",
                    }
                )
        return disks

    def _nics(self) -> List[Dict[str, Any]]:
        nics: List[Dict[str, Any]] = []
        if os.name == "nt":
            data = _powershell_json(
                "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | "
                "Select-Object Name,InterfaceDescription,LinkSpeed | ConvertTo-Json -Compress"
            )
            for row in _as_list(data):
                description = str(row.get("InterfaceDescription") or "").strip()
                name = str(row.get("Name") or "").strip()
                nics.append(
                    {
                        "name": description or name,
                        "interface": name,
                        "speed_mbps": _speed_mbps(row.get("LinkSpeed")),
                    }
                )
            if not nics:
                # PowerShell 不可用时退化为接口名与速率
                try:
                    stats = psutil.net_if_stats()
                except Exception:
                    stats = {}
                for iface, stat in stats.items():
                    if iface.lower() in ("lo", "loopback pseudo-interface 1") or not stat.isup:
                        continue
                    nics.append(
                        {
                            "name": iface,
                            "interface": iface,
                            "speed_mbps": stat.speed if stat.speed and stat.speed > 0 else None,
                        }
                    )
        else:
            try:
                stats = psutil.net_if_stats()
            except Exception:
                stats = {}
            for iface, stat in stats.items():
                if iface.lower() in ("lo", "lo0") or not stat.isup:
                    continue
                vendor = ""
                driver = ""
                uevent = _read_text(Path(f"/sys/class/net/{iface}/device/uevent"))
                if uevent:
                    for line in uevent.splitlines():
                        if line.startswith("DRIVER="):
                            driver = line.split("=", 1)[1].strip()
                vendor_id = _read_text(Path(f"/sys/class/net/{iface}/device/vendor"))
                if vendor_id and vendor_id.lower() in PCI_VENDORS:
                    vendor = PCI_VENDORS[vendor_id.lower()]
                speed = stat.speed if stat.speed and stat.speed > 0 else None
                detail = " ".join(item for item in (vendor, driver) if item)
                nics.append(
                    {
                        "name": detail or iface,
                        "interface": iface,
                        "speed_mbps": speed,
                    }
                )
        if nics:
            physical = [nic for nic in nics if not _looks_virtual_nic(nic)]
            if physical:
                nics = physical
            nics.sort(key=lambda nic: nic.get("speed_mbps") or 0, reverse=True)
        return nics


def sys_platform_is_linux() -> bool:
    return platform.system() == "Linux"
