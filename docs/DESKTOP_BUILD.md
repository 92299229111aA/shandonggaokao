# 桌面版构建与打包

从源码到安装包，一共三步。每步都可以单独跑，产物都能单独验证。

## 前置

* Node + pnpm（前端）、`backend/.venv`（后端）、本地 PostgreSQL（只在导出数据时需要）
* 首次打包后端口、路径都由程序自动决定，不需要配置

## 1. 准备 SQLite 数据（`backend/build/gaokao.db`）

```powershell
cd backend
pwsh -File scripts/build_sqlite.ps1
```

这一步把 PostgreSQL 里的 **reference + admission** 两类数据导出成 SQLite：

```
dump (GAOKAO_DB_MODE=postgres)  PostgreSQL → build/staging/*.jsonl
load (GAOKAO_DB_MODE=sqlite)    staging → build/gaokao.db  + 逐表行数校验
```

必须分两个进程，因为模型的 schema 与外键目标在 import 时就按方言定型了，
一个进程不可能同时是两种方言（详见 `docs/DECISIONS.md`）。

**用户数据不随包**：`candidate` / `recommendation` 两个 schema 从空表开始，
否则安装包里会带着开发机上的测试档案。

## 2. 导出前端静态文件（`frontend/out`）

```powershell
cd frontend
$env:DESKTOP_BUILD='1'
$env:NEXT_PUBLIC_API_BASE_URL='/'   # 用相对路径调 API；空字符串会被当成"未设置"
npx next build
```

`DESKTOP_BUILD=1` 会把 `output` 切成 `export` 并打开 `trailingSlash`。

> 为什么 API 地址是 `/`：桌面版由**同一个后端进程**同时提供前端与 API，
> 所以前端必须走相对路径。哨兵值用 `/` 而不是空串 —— 空串在构建时会被当成未设置，
> 回落到 `http://127.0.0.1:8000`。

## 3. 打后端（`backend/dist/gaokao/`）

```powershell
cd backend
.\.venv\Scripts\pyinstaller.exe --noconfirm --clean --distpath dist --workpath build\pyi packaging\gaokao.spec
```

产出 `dist/gaokao/`（约 167 MB，onedir）。包含：

```
gaokao.exe                     启动器：挑空闲端口 → 起 uvicorn → 打印 READY <url>
_internal\web\                 frontend/out 的副本
_internal\data\gaokao.db       随包数据库
```

> onedir 而不是 onefile：OR-Tools 原生库很大，onefile 每次启动都要解包，冷启动十几秒。

## 4. 打安装包（`desktop/release/`）

```powershell
cd desktop
npm install          # 只需一次
npx electron-builder --dir --win    # 免安装目录，便于验证
npx electron-builder --win nsis     # NSIS 安装包
```

产物：`release/山东高考智能志愿-1.0.0-安装包.exe`（约 127 MB）。

## 运行时行为

```
Electron 主进程
  └─ 启动 resources/backend/gaokao.exe --no-open
       ├─ 首次启动把 data/gaokao.db 复制到 %LOCALAPPDATA%\山东高考智能志愿\
       ├─ 挑一个空闲端口起 uvicorn（同时提供 API 与前端）
       └─ stdout 打印 READY http://127.0.0.1:<port>
  └─ 读到 READY 后开窗口加载该地址
```

* **数据库放用户目录**，不放进安装目录（Program Files 不可写）
* **端口不固定**，避免和用户机器上的 8000 冲突
* **单实例**：重复双击只聚焦已有窗口，不会起第二个后端
* **退出时连带杀掉后端**（Windows 用 `taskkill /T`），不留僵尸进程

## 自动化验证

后端可以脱离界面单独验证：

```powershell
# 起 SQLite 模式的 API（端口 8001），跑 16 项端到端断言
cd backend
$env:GAOKAO_DB_MODE='sqlite'
$env:GAOKAO_DATA_DIR='<含 gaokao.db 的目录>'
.\.venv\Scripts\uvicorn.exe app.main:app --port 8001
.\.venv\Scripts\python.exe scripts\smoke_sqlite.py
```

安装后的程序也可以无人值守验证：

```powershell
$env:GAOKAO_SMOKE='1'          # 加载完成后把结果写到 %TEMP%\gaokao-smoke.txt 并退出
& "C:\...\山东高考智能志愿.exe"
Get-Content "$env:TEMP\gaokao-smoke.txt"
# 期望：SMOKE_OK url=... title=山东高考智能志愿｜专业工作台 shell=...
```

> 注意：这台机器上 `ELECTRON_RUN_AS_NODE=1` 是全局设置的，
> 它会让 Electron 以 Node 模式启动、拿不到 `app`。测试前要 `Remove-Item Env:ELECTRON_RUN_AS_NODE`。
> 用户的电脑上没有这个变量。

## 已知未做

* **代码签名**：安装包与 exe 都未签名，首次运行 Windows 会提示"未知发布者"
* **自动更新**：没有接入 electron-updater
* **安装包体积**：418 MB 安装后 / 127 MB 压缩包，主要是 Electron 与 OR-Tools
