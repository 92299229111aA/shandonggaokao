"""桌面版启动器：在本地起一个进程，同时提供 API 与前端静态文件。

设计取舍：
* **不用固定端口**。用户机器上 8000 很可能被占；启动时挑一个空闲端口，
  把真实地址打到 stdout（`READY http://127.0.0.1:<port>`），Electron 读到后再开窗口。
* **数据放在用户目录**，不放安装目录。Program Files 通常不可写，而数据库要能写
  （用户会建档案、方案）。首次启动时把随包的 gaokao.db 复制过去。
* 前端与数据都通过 `sys._MEIPASS` 定位，保证 PyInstaller 单目录/单文件都能跑。

单独运行（开发调试）：
    python desktop_launcher.py --no-open
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


def _force_utf8_stdio() -> None:
    """Electron 逐行读 stdout，Windows 默认 cp936 会把中文写坏。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def app_root() -> Path:
    """随包资源所在目录（PyInstaller 解包目录或源码目录）。"""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parent


def user_data_dir() -> Path:
    """可写的用户数据目录（数据库、日志）。"""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    target = base / "山东高考智能志愿"
    target.mkdir(parents=True, exist_ok=True)
    return target


def ensure_database(data_dir: Path) -> Path:
    """首次启动把随包数据库复制到用户目录；已存在则复用（用户数据不能丢）。"""
    target = data_dir / "gaokao.db"
    if target.exists() and target.stat().st_size > 0:
        return target

    bundled = Path(os.environ["GAOKAO_BUNDLED_DB"]) if os.environ.get("GAOKAO_BUNDLED_DB") else None
    if bundled is None or not bundled.exists():
        bundled = app_root() / "data" / "gaokao.db"
    if not bundled.exists():
        bundled = app_root() / "gaokao.db"
    if not bundled.exists():
        raise SystemExit(f"找不到随包数据库：{bundled}")

    # 先写临时文件再改名，避免中途失败留下半截数据库
    staging = target.with_suffix(".db.tmp")
    shutil.copy2(bundled, staging)
    staging.replace(target)
    return target


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_until_ready(url: str, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/v1/meta/health", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="山东高考智能志愿 桌面启动器")
    parser.add_argument("--port", type=int, default=0, help="固定端口（默认自动挑选）")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    parser.add_argument(
        "--open-only",
        action="store_true",
        help="不自启服务，只等待已运行的服务（打包后用不到，留给调试）",
    )
    args = parser.parse_args(argv)
    _force_utf8_stdio()

    data_dir = user_data_dir()
    database = ensure_database(data_dir)
    # 允许外部指定（开发调试时直接指向 frontend/out）
    static_dir = Path(os.environ["GAOKAO_STATIC_DIR"]) if os.environ.get("GAOKAO_STATIC_DIR") else app_root() / "web"
    if not (static_dir / "index.html").exists():
        static_dir = app_root() / "out"
    if not (static_dir / "index.html").exists():
        print(f"!! 找不到前端静态文件：{static_dir}", file=sys.stderr)
        return 2

    port = args.port or free_port()
    url = f"http://127.0.0.1:{port}"

    # 这几步必须在导入 app 之前设置：设置是 import 时读的
    os.environ["GAOKAO_DB_MODE"] = "sqlite"
    os.environ["GAOKAO_DATA_DIR"] = str(data_dir)
    os.environ["GAOKAO_STATIC_DIR"] = str(static_dir)
    os.environ["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"

    print(f"数据目录 {data_dir}", flush=True)
    print(f"静态文件 {static_dir}", flush=True)

    if args.open_only:
        if not wait_until_ready(url):
            print(f"!! 服务未就绪：{url}", file=sys.stderr)
            return 1
        print(f"READY {url}", flush=True)
        return 0

    import uvicorn

    from app.main import app

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    if not wait_until_ready(url):
        print(f"!! 服务启动失败：{url}", file=sys.stderr)
        return 1

    # Electron 就是靠这一行拿到地址的
    print(f"READY {url}", flush=True)
    if not args.no_open:
        webbrowser.open(url)

    try:
        while thread.is_alive():
            thread.join(timeout=0.5)
    except KeyboardInterrupt:
        server.should_exit = True
        thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
