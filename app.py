"""SysMon —— 轻量系统监控面板（Python 后端 + 网页前端）。

用法：
    python app.py                     # 监听 127.0.0.1:8088
    python app.py --host 0.0.0.0      # 对外监听（建议放在 HTTPS 反向代理之后）
    python app.py --port 9000 --data-dir ./data
    python app.py --reset-password    # 忘记密码时重置
"""

from __future__ import annotations

import argparse
import getpass
import signal
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:  # Windows 控制台编码兜底
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from core import auth  # noqa: E402
from core.branding import BrandingStore  # noqa: E402
from core.collectors import Collector, GpuProbe  # noqa: E402
from core.config import ConfigError, Store  # noqa: E402
from core.web import AppContext, MonitorServer, make_handler  # noqa: E402

VERSION = "1.3.1"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sysmon",
        description="SysMon 系统监控面板（CPU / 内存 / 磁盘 / GPU / 网络）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8088, help="监听端口，默认 8088")
    parser.add_argument(
        "--data-dir",
        default=str(BASE_DIR / "data"),
        help="数据目录（config.json、初始化令牌），默认 ./data",
    )
    parser.add_argument("--reset-password", action="store_true", help="交互式重置管理员密码")
    parser.add_argument("--version", action="version", version=f"SysMon {VERSION}")
    return parser.parse_args(argv)


def _print_banner(host: str, port: int, data_dir: Path, configured: bool, setup_token) -> None:
    url_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    print("=" * 56)
    print(f" SysMon {VERSION}  系统监控面板")
    print(f" 地址:     http://{url_host}:{port}")
    print(f" 数据目录: {data_dir}")
    print(f" 管理员:   {'已配置' if configured else '未配置（首次使用需初始化）'}")
    if setup_token:
        print("-" * 56)
        print(" 初始化令牌（第一次打开网页时填写）：")
        print(f"   {setup_token}")
        print(f" 同时保存在: {data_dir / 'setup-token.txt'}")
    if host in ("0.0.0.0", "::"):
        print("-" * 56)
        print(" 警告: 正在监听所有网卡，请使用 HTTPS 反向代理或仅在可信内网使用。")
    print("=" * 56)


def reset_password(data_dir: Path) -> int:
    probe = GpuProbe()
    probe.detect()
    store = Store(data_dir / "config.json", probe.supported)
    record = store.admin()
    if not record:
        print("尚未初始化管理员账户，请先启动服务并在网页中完成初始化。")
        return 1
    username = str(record.get("username") or "admin")
    print(f"重置管理员 {username} 的密码（修改后需重启服务生效）")
    first = getpass.getpass("新密码: ")
    if len(first) < 8:
        print("密码长度至少 8 位。")
        return 1
    if first != getpass.getpass("再次输入: "):
        print("两次输入不一致。")
        return 1
    store.set_admin(auth.hash_password(first, username))
    print("密码已更新。请重启服务，并用新密码登录。")
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    data_dir = Path(args.data_dir).expanduser().resolve()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"无法创建数据目录 {data_dir}: {exc}")
        return 1

    if args.reset_password:
        return reset_password(data_dir)

    collector = Collector()
    collector.gpu.detect()  # 先探测，决定首次默认是否启用 GPU 模块
    try:
        store = Store(data_dir / "config.json", collector.gpu.supported)
    except ConfigError as exc:
        print(f"配置加载失败: {exc}")
        return 1

    setup_token = None
    if not store.configured:
        setup_token = auth.ensure_setup_token(data_dir / "setup-token.txt")
    else:
        # 已经初始化过，清理可能遗留的令牌文件
        auth.remove_setup_token(data_dir / "setup-token.txt")

    collector.start()

    ctx = AppContext(
        store=store,
        collector=collector,
        branding=BrandingStore(data_dir),
        static_dir=BASE_DIR / "static",
        data_dir=data_dir,
        setup_token=setup_token,
        version=VERSION,
    )
    handler = make_handler(ctx)
    try:
        server = MonitorServer((args.host, args.port), handler)
    except OSError as exc:
        print(f"无法监听 {args.host}:{args.port} —— {exc}")
        collector.stop()
        return 1

    _print_banner(args.host, args.port, data_dir, store.configured, setup_token)

    def _shutdown(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _shutdown)
        except (ValueError, OSError):
            pass

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        server.server_close()
        collector.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
