"""系统指标采集：CPU / 内存 / 磁盘 / GPU / 网络。

设计要点：
* 采样线程每 1 秒采一次（CPU 使用率用 psutil 的阻塞式采样，取值更准确）；
* GPU 由独立线程每 5 秒调用一次 nvidia-smi，避免阻塞主采样；
* 历史序列保存在环形缓冲中，供前端画波形。
"""

from __future__ import annotations

import collections
import copy
import json
import os
import shutil
import statistics
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import psutil
except ImportError as exc:  # pragma: no cover - 启动时的友好提示
    raise SystemExit(
        "缺少依赖 psutil。请先运行：python -m pip install -r requirements.txt"
    ) from exc

from .hardware import HardwareInfo, host_info

SERIES = ("cpu", "memory", "disk", "gpu", "net_down", "net_up")
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# snap 的 squashfs 镜像和 Docker 的 overlay 等不是真实磁盘分区；
# 它们经常接近 100% 满，如果参与「最高占用分区」会把整机磁盘显示成 100%。
_SKIP_DISK_FSTYPES = {
    "cdrom",
    "iso9660",
    "udf",
    "squashfs",
    "overlay",
    "tmpfs",
    "devtmpfs",
    "ramfs",
    "proc",
    "sysfs",
    "cgroup",
    "cgroup2",
}
_SKIP_DISK_PREFIXES = ("/snap/",)


def _is_physical_partition(part: Any) -> bool:
    fstype = (getattr(part, "fstype", "") or "").lower()
    device = getattr(part, "device", "") or ""
    mountpoint = getattr(part, "mountpoint", "") or ""
    if fstype in _SKIP_DISK_FSTYPES:
        return False
    if not fstype and not device.startswith("/dev/"):
        return False
    if device.startswith(("/dev/loop", "/dev/ram", "/dev/zram")):
        return False
    if mountpoint in ("/snap",) or mountpoint.startswith(_SKIP_DISK_PREFIXES):
        return False
    return True


def _cpu_temp() -> Optional[float]:
    sensors = getattr(psutil, "sensors_temperatures", None)
    if not sensors:
        return None
    try:
        temps = sensors()
    except Exception:
        return None
    for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
        entries = temps.get(key)
        if entries:
            try:
                return round(float(entries[0].current), 1)
            except (TypeError, ValueError):
                continue
    return None


def _find_nvidia_smi() -> Optional[str]:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    candidates = []
    if os.name == "nt":
        candidates += [
            r"C:\Windows\System32\nvidia-smi.exe",
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        ]
    else:
        candidates += [
            "/usr/bin/nvidia-smi",
            "/usr/local/bin/nvidia-smi",
            "/usr/local/nvidia/bin/nvidia-smi",
            "/opt/nvidia/bin/nvidia-smi",
        ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _num(parts: List[str], index: int) -> Optional[float]:
    if index >= len(parts):
        return None
    raw = parts[index].strip()
    if raw in ("", "N/A", "[N/A]", "[Not Supported]", "[Unknown Error]"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_gpu_csv(text: str) -> List[Dict[str, Any]]:
    devices: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        devices.append(
            {
                "index": parts[0],
                "name": parts[1],
                "vendor": "nvidia",
                "util": _num(parts, 2),
                "mem_used_mb": _num(parts, 3),
                "mem_total_mb": _num(parts, 4),
                "temp_c": _num(parts, 5),
                "power_w": _num(parts, 6),
                "fan": _num(parts, 7),
            }
        )
    return devices


def _nvidia_query(smi: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    if not smi:
        return None
    query = (
        "index,name,utilization.gpu,memory.used,memory.total,"
        "temperature.gpu,power.draw,fan.speed"
    )
    cmd = [smi, f"--query-gpu={query}", "--format=csv,noheader,nounits"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=4, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return _parse_gpu_csv(proc.stdout)


def _read_text_file(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _read_number(path: Path) -> Optional[float]:
    raw = _read_text_file(path)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _amd_hwmon(dev: Path) -> Tuple[Optional[float], Optional[float]]:
    temp = power = None
    root = dev / "hwmon"
    if not root.is_dir():
        return None, None
    for hw in sorted(root.glob("hwmon*")):
        raw = _read_number(hw / "temp1_input")
        if raw is not None and temp is None:
            temp = raw / 1000.0
        value = _read_number(hw / "power1_average")
        if value is None:
            value = _read_number(hw / "power1_input")
        if value is not None and power is None:
            power = value / 1_000_000.0
    return temp, power


def _amd_devices(base: str = "/sys/class/drm") -> Optional[List[Dict[str, Any]]]:
    """通过 amdgpu 的 sysfs 读取 AMD GPU，无需 rocm-smi 或额外依赖。"""
    root = Path(base)
    if not root.is_dir():
        return None
    devices: List[Dict[str, Any]] = []
    seen = set()
    for card in sorted(root.glob("card[0-9]*")):
        dev = card / "device"
        vendor = _read_text_file(dev / "vendor")
        if not vendor or vendor.lower() != "0x1002":
            continue
        busy_file = dev / "gpu_busy_percent"
        if not busy_file.exists():
            continue
        real = os.path.realpath(str(dev))
        if real in seen:
            continue
        seen.add(real)

        util = _read_number(busy_file)
        used = _read_number(dev / "mem_info_vram_used")
        total = _read_number(dev / "mem_info_vram_total")
        temp, power = _amd_hwmon(dev)
        name = _read_text_file(dev / "product_name") or ""
        if not name:
            pci = ""
            for line in (_read_text_file(dev / "uevent") or "").splitlines():
                if line.startswith("PCI_ID="):
                    pci = line.split("=", 1)[1].strip()
            name = f"AMD GPU ({pci})" if pci else "AMD GPU"
        devices.append(
            {
                "index": card.name.replace("card", ""),
                "name": name,
                "vendor": "amd",
                "util": round(util, 1) if util is not None else None,
                "mem_used_mb": round(used / 1048576, 1) if used is not None else None,
                "mem_total_mb": round(total / 1048576, 1) if total is not None else None,
                "temp_c": round(temp, 1) if temp is not None else None,
                "power_w": round(power, 1) if power is not None else None,
                "fan": None,
            }
        )
    return devices or None


def _parse_intel_gpu_top(text: str) -> Optional[Dict[str, Any]]:
    samples = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            samples.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    sample = samples[-1] if samples else None
    if sample is None:
        try:
            sample = json.loads((text or "").strip())
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(sample, dict):
        return None
    engines = sample.get("engines") or {}
    busy = [
        float(item["busy"])
        for item in engines.values()
        if isinstance(item, dict) and isinstance(item.get("busy"), (int, float))
    ]
    if not busy:
        return None
    freq = sample.get("frequency") or {}
    power = sample.get("power") or {}
    freq_mhz = freq.get("actual") if isinstance(freq.get("actual"), (int, float)) else None
    power_w = power.get("gpu") if isinstance(power.get("gpu"), (int, float)) else None
    return {
        "index": "0",
        "name": "Intel GPU",
        "vendor": "intel",
        "util": round(max(busy), 1),
        "mem_used_mb": None,
        "mem_total_mb": None,
        "temp_c": None,
        "power_w": power_w,
        "fan": None,
        "freq_mhz": freq_mhz,
    }


def _intel_gpu_top_sample(tool: Optional[str]) -> Optional[Dict[str, Any]]:
    if not tool:
        return None
    try:
        proc = subprocess.run(
            [tool, "-J", "-s", "500"],
            capture_output=True,
            text=True,
            timeout=2.0,
            creationflags=_NO_WINDOW,
        )
        out = proc.stdout or ""
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return None
    return _parse_intel_gpu_top(out)


class GpuProbe(threading.Thread):
    """多厂商 GPU 探测。

    * NVIDIA：nvidia-smi（Linux / Windows）
    * AMD：amdgpu 的 /sys/class/drm sysfs（Linux，无需 rocm-smi）
    * Intel：intel_gpu_top -J（Linux，需要 intel-gpu-tools 与 perf 权限）
    """

    def __init__(self, interval: float = 5.0):
        super().__init__(name="gpu-probe", daemon=True)
        self._stop_event = threading.Event()
        self._interval = interval
        self._lock = threading.Lock()
        self.smi = _find_nvidia_smi()
        self.intel_tool = shutil.which("intel_gpu_top") if os.name != "nt" else None
        self.vendor = ""
        self.supported = False
        self.reason = "尚未检测"
        self.devices: List[Dict[str, Any]] = []
        self.last_ok = 0.0
        self.last_error = ""

    # ---------- 探测 ----------
    def detect(self) -> None:
        devices: List[Dict[str, Any]] = []
        vendor = ""
        nvidia_hint = "未找到 nvidia-smi"
        amd_hint = "未检测到 amdgpu"
        intel_hint = "未安装 intel-gpu-tools"

        if self.smi:
            result = _nvidia_query(self.smi)
            if result:
                devices, vendor = result, "nvidia"
            else:
                nvidia_hint = "nvidia-smi 未返回设备"

        if not devices and os.name != "nt":
            amd = _amd_devices()
            if amd:
                if any(item.get("util") is not None for item in amd):
                    devices, vendor = amd, "amd"
                else:
                    amd_hint = "检测到 amdgpu，但读不到 gpu_busy_percent"
            if not devices:
                if self.intel_tool:
                    sample = _intel_gpu_top_sample(self.intel_tool)
                    if sample:
                        devices, vendor = [sample], "intel"
                    else:
                        intel_hint = "intel_gpu_top 无输出（可能需要 root / perf 权限）"

        with self._lock:
            if devices:
                self.vendor = vendor
                self.supported = True
                self.reason = ""
                self.devices = devices
                self.last_ok = time.time()
                self.last_error = ""
                return
            self.vendor = ""
            self.supported = False
            self.devices = []
            if os.name == "nt":
                self.reason = f"未检测到可采集的 GPU（Windows 下仅支持 NVIDIA：{nvidia_hint}）"
            else:
                self.reason = (
                    "未检测到可采集的 GPU"
                    f"（NVIDIA：{nvidia_hint}；AMD：{amd_hint}；Intel：{intel_hint}）"
                )

    def rescan(self) -> None:
        self.smi = _find_nvidia_smi()
        self.intel_tool = shutil.which("intel_gpu_top") if os.name != "nt" else None
        self.detect()

    # ---------- 后台刷新 ----------
    def run(self) -> None:
        while not self._stop_event.wait(self._interval):
            with self._lock:
                supported = self.supported
                vendor = self.vendor
            if not supported:
                continue
            devices: Optional[List[Dict[str, Any]]] = None
            if vendor == "nvidia":
                devices = _nvidia_query(self.smi)
            elif vendor == "amd":
                devices = _amd_devices()
            elif vendor == "intel":
                sample = _intel_gpu_top_sample(self.intel_tool)
                devices = [sample] if sample else None
            if devices:
                with self._lock:
                    self.devices = devices
                    self.last_ok = time.time()
                    self.last_error = ""

    def stop(self) -> None:
        self._stop_event.set()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "supported": self.supported,
                "vendor": self.vendor,
                "reason": self.reason,
                "devices": copy.deepcopy(self.devices),
                "last_ok": self.last_ok,
                "error": self.last_error,
            }


class Collector:
    def __init__(self, sample_interval: float = 1.0, history: int = 120):
        self.interval = sample_interval
        self.history_len = history
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.gpu = GpuProbe()
        self.hardware = HardwareInfo()
        self.host = host_info()
        self._history: Dict[str, collections.deque] = {
            key: collections.deque(maxlen=history) for key in SERIES
        }
        self._latest: Optional[Dict[str, Any]] = None
        self._error = ""
        self._prev_net: Optional[Tuple[int, int]] = None
        self._prev_disk: Optional[Tuple[int, int]] = None
        self._prev_ts: Optional[float] = None
        self._parts: List[Any] = []
        self._parts_ts = 0.0
        self.started_at = time.time()
        psutil.cpu_percent(interval=None)  # 预热，避免首次读数恒为 0

    # ---------- 生命周期 ----------
    def start(self) -> None:
        self.gpu.detect()
        if not self.gpu.is_alive():
            self.gpu.start()
        self.hardware.start()
        try:
            self._sample(0.25)
        except Exception as exc:  # 首次采样失败不应阻止服务启动
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"
        self._thread = threading.Thread(target=self._run, name="collector", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self.gpu.stop()

    def rescan_gpu(self) -> None:
        self.gpu.rescan()

    # ---------- 采样 ----------
    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._sample(self.interval)
            except Exception as exc:
                with self._lock:
                    self._error = f"{type(exc).__name__}: {exc}"
                time.sleep(1.0)

    def _sample(self, interval: float) -> None:
        per_core = psutil.cpu_percent(interval=interval, percpu=True) or []
        now = time.monotonic()
        dt = (now - self._prev_ts) if self._prev_ts else 0.0

        # CPU
        per_core = [round(float(x), 1) for x in per_core]
        total = round(statistics.fmean(per_core), 1) if per_core else 0.0
        freq_ghz = None
        try:
            freq = psutil.cpu_freq()
            if freq and freq.current:
                freq_ghz = round(freq.current / 1000.0, 2)
        except Exception:
            pass
        load = None
        try:
            load = [round(float(x), 2) for x in psutil.getloadavg()]
        except Exception:
            pass
        try:
            processes = len(psutil.pids())
        except Exception:
            processes = None
        cpu = {
            "percent": total,
            "per_core": per_core,
            "cores": psutil.cpu_count(logical=True) or len(per_core) or 1,
            "physical": psutil.cpu_count(logical=False),
            "freq_ghz": freq_ghz,
            "load": load,
            "temp_c": _cpu_temp(),
            "processes": processes,
        }

        # 内存
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        memory = {
            "percent": round(float(vm.percent), 1),
            "used": int(vm.used),
            "total": int(vm.total),
            "available": int(vm.available),
            "swap_percent": round(float(swap.percent), 1),
            "swap_used": int(swap.used),
            "swap_total": int(swap.total),
        }

        # 磁盘
        parts = self._disk_parts()
        rows: List[Dict[str, Any]] = []
        used_sum = 0
        total_sum = 0
        for part in parts:
            try:
                du = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            rows.append(
                {
                    "mount": part.mountpoint,
                    "device": part.device,
                    "fstype": part.fstype,
                    "percent": round(float(du.percent), 1),
                    "used": int(du.used),
                    "total": int(du.total),
                }
            )
            used_sum += du.used
            total_sum += du.total
        if not rows:
            # 容器 / 特殊文件系统下可能拿不到分区列表，退化为根目录占用
            try:
                root = os.path.abspath(os.sep)
                du = psutil.disk_usage(root)
                rows.append(
                    {
                        "mount": root,
                        "device": root,
                        "fstype": "",
                        "percent": round(float(du.percent), 1),
                        "used": int(du.used),
                        "total": int(du.total),
                    }
                )
                used_sum += du.used
                total_sum += du.total
            except OSError:
                pass
        rows.sort(key=lambda r: r["percent"], reverse=True)
        max_percent = max((r["percent"] for r in rows), default=0.0)
        # 以占用最高的分区代表整机磁盘水位，波形与表盘保持一致
        disk_percent = max_percent

        read_bps = write_bps = 0.0
        try:
            io = psutil.disk_io_counters()
        except Exception:
            io = None
        if io is not None:
            if self._prev_disk is not None and dt > 0:
                read_bps = max(0.0, (io.read_bytes - self._prev_disk[0]) / dt)
                write_bps = max(0.0, (io.write_bytes - self._prev_disk[1]) / dt)
            self._prev_disk = (io.read_bytes, io.write_bytes)
        disk = {
            "percent": disk_percent,
            "max_percent": max_percent,
            "used": used_sum,
            "total": total_sum,
            "parts": rows,
            "read_bps": round(read_bps, 1),
            "write_bps": round(write_bps, 1),
        }

        # 网络
        rx, tx = self._net_totals()
        down_bps = up_bps = 0.0
        if self._prev_net is not None and dt > 0:
            down_bps = max(0.0, (rx - self._prev_net[0]) / dt)
            up_bps = max(0.0, (tx - self._prev_net[1]) / dt)
        self._prev_net = (rx, tx)
        net = {
            "down_bps": round(down_bps, 1),
            "up_bps": round(up_bps, 1),
            "rx_total": rx,
            "tx_total": tx,
        }

        # GPU
        gpu = self.gpu.snapshot()
        utils = [d["util"] for d in gpu["devices"] if d.get("util") is not None]
        gpu["util"] = round(max(utils), 1) if utils else None

        self._prev_ts = now
        latest = {
            "ts": time.time(),
            "uptime": max(0, int(time.time() - psutil.boot_time())),
            "cpu": cpu,
            "memory": memory,
            "disk": disk,
            "gpu": gpu,
            "net": net,
        }
        with self._lock:
            self._latest = latest
            self._error = ""
            self._history["cpu"].append(cpu["percent"])
            self._history["memory"].append(memory["percent"])
            self._history["disk"].append(disk["percent"])
            self._history["gpu"].append(gpu["util"])
            self._history["net_down"].append(net["down_bps"])
            self._history["net_up"].append(net["up_bps"])

    # ---------- 工具 ----------
    def _disk_parts(self) -> List[Any]:
        now = time.monotonic()
        if not self._parts or now - self._parts_ts > 60:
            try:
                parts = psutil.disk_partitions(all=False)
            except Exception:
                parts = []
            self._parts = [p for p in parts if _is_physical_partition(p)]
            self._parts_ts = now
        return self._parts

    def _net_totals(self) -> Tuple[int, int]:
        try:
            interfaces = psutil.net_io_counters(pernic=True) or {}
        except Exception:
            return 0, 0
        rx = tx = 0
        for name, counters in interfaces.items():
            low = name.lower()
            if (
                "loopback" in low
                or low in ("lo", "lo0")
                or low.startswith(("veth", "docker", "br-"))
            ):
                continue
            rx += counters.bytes_recv
            tx += counters.bytes_sent
        return rx, tx

    # ---------- 输出 ----------
    def snapshot(self) -> Dict[str, Any]:
        hardware = self.hardware.snapshot()
        with self._lock:
            if self._latest is None:
                return {
                    "ts": time.time(),
                    "warming_up": True,
                    "error": self._error,
                    "history": {key: [] for key in SERIES},
                    "hardware": hardware,
                }
            data = copy.deepcopy(self._latest)
            data["history"] = {key: list(values) for key, values in self._history.items()}
            data["error"] = self._error
            data["started_at"] = self.started_at
            data["hardware"] = hardware
            return data
