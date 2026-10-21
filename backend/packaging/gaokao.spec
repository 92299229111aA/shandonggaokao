# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把后端 + 前端静态文件 + SQLite 数据打成一个可分发目录。

为什么用 onedir 而不是 onefile：OR-Tools 的原生库有几百 MB，onefile 每次启动都要
把全部内容解包到临时目录，冷启动要十几秒；onedir 只解包一次，启动快得多。

打包产出 dist/gaokao/，Electron 会把这个目录作为 resource 一起装进安装包。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # backend/（SPECPATH 就是 packaging 目录）
REPO = ROOT.parent

# 前端静态导出（先跑 DESKTOP_BUILD=1 的 next build）
WEB_DIR = REPO / "frontend" / "out"
# 随包数据库（先跑 scripts/build_sqlite.ps1）
DB_FILE = ROOT / "build" / "gaokao.db"

if not (WEB_DIR / "index.html").exists():
    raise SystemExit(
        "缺少前端静态文件：先执行  cd frontend; $env:DESKTOP_BUILD='1'; $env:NEXT_PUBLIC_API_BASE_URL='/'; npx next build"
    )
if not DB_FILE.exists():
    raise SystemExit("缺少 SQLite 数据文件：先执行  pwsh -File scripts/build_sqlite.ps1")

datas = [
    (str(WEB_DIR), "web"),
    (str(DB_FILE), "data"),
]

# OR-Tools 的 CP-SAT 通过 C 扩展加载原生库，PyInstaller 静态分析看不到
binaries = collect_dynamic_libs("ortools")

hiddenimports = [
    # uvicorn 在运行期按配置字符串动态导入
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    # 引擎通过 SQLAlchemy 的字符串方言名加载
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.pysqlite",
    "ortools.sat.python.cp_model",
]

# 插件/模型是通过 import 副作用注册的，静态分析发现不了
hiddenimports += collect_submodules("app")

# 这些库要么没用到，要么在桌面端不可用；排掉能显著减小体积。
# 注意：pandas 不能排 —— ortools.sat.python.cp_model 在 import 时就依赖它。
excludes = [
    "tkinter",
    "matplotlib",
    "notebook",
    "IPython",
    "pytest",
    "mypy",
    "ruff",
    "psycopg",  # 桌面版只用 SQLite
    "psycopg_binary",
    "alembic",
]

a = Analysis(
    [str(ROOT / "desktop_launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="gaokao",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # Electron 要靠 stdout 拿 READY 地址
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="gaokao",
)
