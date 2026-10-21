# 离线桌面版实施方案（决策记录 + 分阶段执行提示词）

> 状态：**待确认**，确认后按「推荐顺序」执行。
> 编写日期：2026-10-21
> 关联文档：`docs/DECISIONS.md`（D-001…D-033）、`docs/KNOWN_LIMITATIONS.md`、`docs/ARCHITECTURE.md`

---

## 一、目标

把现有的 Web 版（FastAPI + PostgreSQL + Next.js）改造成**双击即用的 Windows 桌面软件**：

- **完全离线可用**：断网也能打开、走完全流程（成绩 → 偏好 → 推荐 → 编辑器 → 体检 → 导出）
- **桌面窗口形态**：不经过浏览器，不出现命令行窗口
- **安装包分发**：`.exe` 安装程序 + 免安装 zip 两份
- **界面重做**：专业稳重风格，换皮 + 关键页面重排 + 数据可视化与动效
- **数据可更新**：启动时可选联网检查更新，失败自动降级用本地数据

**不做**：应用商店上架、手机端、macOS/Linux、深色模式、多用户账号体系。

---

## 二、决策总表

| # | 维度 | 决定 | 备注 |
|---|---|---|---|
| D1 | 使用者 | 自己 / 亲友内部使用 | 不上商店，不需要软著、ICP 备案、隐私合规 |
| D2 | 平台 | 仅 Windows 10/11 64 位 | 不需要 Mac；结构上不预留 macOS 构建 |
| D3 | 离线要求 | **完全离线可用** | 引擎随包内嵌，只监听 `127.0.0.1` |
| D4 | 手机端 | **暂不做** | PC 稳定后再议 |
| D5 | 后端形态 | **不重写**，Python 引擎内嵌为子进程 | Python 14,266 行；CP-SAT 无 JS 版本 |
| D6 | 桌面壳 | **Electron** | 生态成熟、调试方便 |
| D7 | 窗口加载方式 | Electron 加载 `http://127.0.0.1:<port>/` | Python 同时供 API 与静态页面，避开 `file://` 与跨域 |
| D8 | 数据库 | **先试 SQLite**，绕不过则退回内嵌 PostgreSQL | 见 §四 与 §五 风险评估 |
| D9 | 视觉方向 | **专业稳重（教育/政务风）** | 蓝灰主色、弱阴影、强数据层级 |
| D10 | 改造深度 | 换皮 + 关键页面重排 + 数据可视化与动效 | 三层都做 |
| D11 | 深色模式 | **不做** | 专注把浅色做透 |
| D12 | 冷启动 | 启动画面遮住，接受 15–20 秒 | 不做懒加载优化（可随时追加） |
| D13 | 数据更新 | 启动时联网检查（可关），失败降级本地 | 来源 GitHub Releases |
| D14 | 档案管理 | **档案列表页**（新建/重命名/删除，记住上次） | 顺带清理现存 31 个验证档案 |
| D15 | 导出 | **PDF + Excel** 两种 | PDF 用 Electron `printToPDF`，Excel 用 openpyxl |
| D16 | 安装包形态 | 安装程序 `.exe` + 免安装 zip | 两份都出 |
| D17 | 应用名 | **山东高考智能志愿**（沿用） | 安装名、窗口标题、快捷方式一致 |
| D18 | 排期 | 无硬时间点，质量优先 | 每阶段可验收 |
| D19 | 设计流程 | **先出静态设计稿 → 确认 → 再改代码** | 设计稿是独立 HTML 预览页，不碰产品代码 |

---

## 三、目标形态

```
┌─────────────────────────────── Windows 10/11 ───────────────────────────────┐
│                                                                              │
│  ┌──────────────────────┐        ┌────────────────────────────────────────┐  │
│  │   Electron 主进程     │        │   Python 引擎（PyInstaller 冻结）       │  │
│  │   - 启动画面窗口      │  IPC   │   - FastAPI (仅 127.0.0.1:<动态端口>)   │  │
│  │   - 主窗口 BrowserWindow ───────▶   - 静态页面 (StaticFiles)            │  │
│  │   - printToPDF        │        │   - Rule Engine / ACS / CP-SAT          │  │
│  │   - 自动更新下载       │        │   - SQLite 数据文件（用户目录）          │  │
│  └──────────────────────┘        └────────────────────────────────────────┘  │
│           │                                        │                         │
│           └──────────── 首次启动：解压数据库 ──────┘                         │
│                                                                              │
│  数据目录：%LOCALAPPDATA%\山东高考智能志愿\                                     │
│    ├─ gaokao.db        （SQLite，随包分发，首次启动复制）                        │
│    ├─ logs\            （引擎日志，便于排查）                                   │
│    └─ update\          （下载的数据包与临时文件）                                │
└──────────────────────────────────────────────────────────────────────────────┘
```

**关键点**：只有一条网络边界 —— `127.0.0.1`。外网仅用于可选的更新检查。

---

## 四、为什么这么选

### 4.1 为什么不重写成纯前端

| 事实 | 数值 |
|---|---|
| Python 代码量 | 14,266 行 / 82 文件 |
| TypeScript 代码量 | 3,954 行 / 23 文件 |
| OR-Tools CP-SAT | **无 JavaScript 版本**，只有 C++/Python/Java/C#/Go |

组合优化器（`app/optimizer/`）是系统的核心能力之一，重写意味着换成自写启发式，
产出的 96 条方案会与现在不同，且「同输入 → 同输出」的可复现性无法保证。
因此 **Python 引擎保留**，只把「用户能否看见它」变成「看不见」。

### 4.2 为什么是 Electron 而不是 pywebview / Tauri

已选定 Electron（生态成熟、调试方便）。代价是安装包多约 150MB（Chromium），
这在 300MB 量级的包上可以接受。**没有采用 pywebview/Tauri 的另一个原因**是
Electron 的 `printToPDF` 直接满足 PDF 导出需求。

### 4.3 为什么是 SQLite 而不是内嵌 PostgreSQL

| | SQLite | 内嵌 PostgreSQL |
|---|---|---|
| 安装包增量 | ~0（单文件数据库） | +350MB |
| 额外进程 | 无 | 需要 initdb + 常驻进程 + 端口 |
| 与用户机器已有 PG 冲突 | 无 | 有风险 |
| 杀软误报 | 低 | 中 |
| 代码改动 | 少量（见 §4.4） | 零 |

**已实测的有利条件**：

1. 代码里**没有任何 JSONB 运算符 SQL**（`->>`、`@>`、`jsonb_*`）—— JSON 只在 Python 侧读写
2. `CREATE SCHEMA` **只出现在 1 个迁移文件**（`alembic/versions/0001_schemas.py`），其余迁移依赖它
3. 33 张表的 schema 是**模块级常量** `SCHEMA = "reference"` 而非字符串字面量
4. `search_path` 的设置**集中在 `app/db/session.py::set_schemas()` 一个函数**
5. 前端没有任何硬编码的 `localhost` 地址（全部走 `NEXT_PUBLIC_API_BASE_URL`）

### 4.4 SQLite 适配的确切改动面

| 文件 | 改动 | 规模 |
|---|---|---|
| `app/reference/models.py` | `SCHEMA = "reference"` → 可配置 | 1 行 |
| `app/admission/models.py` | 同上 + JSONB 双模式 | 1 行 + 3 列类型 |
| `app/candidate/models.py` | 同上 + JSONB 双模式 | 1 行 + 5 列类型 |
| `app/recommendation/models.py` | 同上 + JSONB 双模式 | 1 行 + 3 列类型 |
| `app/db/session.py` | SQLite 时跳过 `set_schemas`、加 `check_same_thread=False` | ~10 行 |
| `app/config.py` | 增加 `db_mode`（postgres / sqlite）与数据目录设置 | ~10 行 |
| `app/main.py` | 挂载静态页面（桌面模式） | ~10 行 |

**JSONB 统一改法**（11 个字段，分布在 3 个文件）：

```python
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

# PostgreSQL 仍用 JSONB；SQLite 退化为 JSON（文本存储，行为一致）
JSONType = JSON().with_variant(JSONB, "postgresql")
```

**迁移策略**：桌面模式**不使用 Alembic**，改为 `Base.metadata.create_all()`；
正式数据在构建期由 PostgreSQL 导出为 SQLite 文件，随安装包分发。

---

## 五、风险登记

| # | 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|---|
| R1 | SQLite 适配遇到绕不过的兼容问题 | 中 | 高 | 已批准的退路：改用内嵌 PostgreSQL（安装包约 600MB，零代码改动） |
| R2 | PyInstaller 或 Electron 被 Windows Defender 误报 | 高 | 中 | 文档说明手动加白名单；不签名（内部工具不值得买证书） |
| R3 | 冷启动 15–20 秒让人难受 | 高 | 中 | 启动画面 + 明确进度文案；懒加载改造约半天可压到 3–5 秒，随时可加 |
| R4 | 打包后某个隐式导入缺失，运行时才报错 | 中 | 高 | 打包后必须走完整流程冒烟（见 §七 验收清单），不能只看窗口起来 |
| R5 | 数据陈旧被误当当年数据 | 中 | 高 | 界面显眼处标「数据截至 2026 年 6 月」 |
| R6 | GitHub Releases 国内下载失败 | 高 | 低 | 下载失败静默降级用本地数据，并在「关于」里提供手动导入 |
| R7 | 运行时剔除 ETL 依赖（pandas/polars）时误伤运行路径 | 中 | 中 | 先在临时 venv 里跑通再打包；打包后用冒烟脚本验证 |
| R8 | 美化改坏功能（编辑器 1000+ 行） | 中 | 高 | 每阶段重跑 52 项 E2E + 浏览器走查；设计落地与逻辑改动分开提交 |

---

## 六、待补事项（阻塞项）

| # | 事项 | 阻塞什么 | 说明 |
|---|---|---|---|
| T1 | **2026 普通类一段达线人数** | 2026 录取数据能否参与 ACS | 上轮遗留。不补则 2026 的 18,074 条录取位次始终是惰性的，安装包里的算法只用 2022–2025 四年历史。补上后一条命令激活 |
| T2 | 应用图标源文件（512×512 PNG 或 SVG） | 安装包图标、窗口图标 | 若无，先用一个「鲁」字标占位 |
| T3 | 数据包托管仓库确认 | 自动更新 | 已定 GitHub Releases，需确认用哪个仓库/Release 标签命名 |

---

## 七、阶段划分与推荐顺序

### 7.1 推荐执行顺序

> **设计稿 → 阶段 1 → 阶段 2 → 阶段 3**

**理由**：

1. **设计稿排最前**（约 1 天）：它是纯静态 HTML 预览，不碰产品代码，你能最早看到风格。
   若风格需要返工，此时成本最低；等到阶段 2 落地后再推翻，代价是重写组件。
2. **阶段 1 排第二**：SQLite 是全局最大不确定性（R1）。越早暴露越好——
   如果绕不过，整个方案要退回内嵌 PostgreSQL，**这会影响阶段 2/3 的排期预期**，
   必须在美化投入之前确定。
3. 阶段 2 依赖设计稿已确认；阶段 3 依赖阶段 2 的组件与布局稳定。

> 备选顺序：若你更想先拿到「能装的 exe」，可把阶段 1 提到设计稿之前。
> 代价是风格确认晚 1 天，但不影响阶段 2 的开工时间。

### 7.2 阶段总览

| 阶段 | 产出 | 预估 | 验收方式 |
|---|---|---|---|
| **设计稿** | 一页静态 HTML 预览（色板/字体/组件/两页布局稿） | 1 天 | 你打开浏览器看，说「可以」或提修改 |
| **阶段 1** | 断网可用的 `.exe`（界面仍旧） | 3–5 天 | 装到你机器上，断网走完全流程 |
| **阶段 2** | 新视觉 + 页面重排 + 档案列表页 | 5–7 天 | 同上，且 52 项 E2E 全过 |
| **阶段 3** | 图表动效 + PDF/Excel 导出 + 自动更新 | 3–5 天 | 导出文件能打开、更新能降级 |

---

## 八、分阶段执行提示词

> 用法：每个提示词都是**自包含**的，可直接复制给一个新的 agent 会话执行。
> 每个提示词都以「不可破坏的约束」开头，防止执行的 agent 为了完成任务而破坏产品规则。

---

### 提示词 P0 —— 静态设计稿（不改产品代码）

```text
【任务】为「山东高考智能志愿」做一版静态设计稿，输出单个 HTML 文件，供用户确认风格。
不要修改任何产品代码（frontend/ 与 backend/ 下现有文件一律不动）。

【项目背景】
- 路径：D:\山东高考
- 产品：山东高考志愿填报辅助工具（单用户本地工具）
- 现有前端：Next.js 15 + TypeScript + Tailwind + shadcn 风格，7 个页面
- 现有视觉：主色 hsl(221 83% 45%) 正蓝、中性灰、圆角 0.6rem、5 个风险层专属色
- 目标风格：**专业稳重（教育/政务风）** —— 蓝灰主色、弱化阴影、强化数据层级，
  让家长和老师看得懂、有信任感；不要花哨、不要渐变炫技、不要插画堆砌

【必须包含的内容】
1. 色板：主色、辅助色、中性色阶、语义色（成功/警告/危险/信息）、
   5 个风险层颜色（高冲/冲/边界/稳/保），每色标出 hex 与对比度说明
2. 字体阶梯：页面标题 / 区块标题 / 正文 / 辅助文字 / 数字（表格与位次用等宽数字）
3. 间距与圆角规范：4/8/12/16/24/32 的间距节奏，圆角与阴影的具体值
4. 组件样例（每个都要有真实内容，不要 Lorem Ipsum）：
   - 按钮：主/次/危险/禁用 四态
   - 卡片：普通 / 带风险层左边条 / 选中
   - 风险层徽标：高冲/冲/边界/稳/保 五种
   - 表单：输入框 / 下拉 / 三段式选科选择 / 校验错误态
   - 表格：志愿方案行（序号、院校、专业、风险层、ACS、学费、计划数）
   - 空状态 / 加载骨架 / 错误提示
5. 两个页面的布局稿（用真实山东数据内容，不要占位符）：
   - 「推荐结果」页：筛选栏、结果卡片列表、分页
   - 「志愿编辑器」页：左侧候选池（含「列表 / 按学校」切换）、右侧我的方案（96 条）、
     两侧各自的筛选栏
6. 一条强制免责声明样例：「ACS 是录取安全指数，不是录取概率」——放在显眼但不喧宾夺主的位置

【技术要求】
- 单文件 HTML + 内联 CSS（可内联少量 JS 仅用于 tab 切换演示），不引外部 CDN、不引字体文件
- 中文字体用系统字体栈（"Microsoft YaHei", "PingFang SC", sans-serif）
- 页面宽度按桌面 1440px 设计，同时给出 1280px 下的表现
- 所有颜色必须能通过 WCAG AA 对比度（正文 4.5:1，大字号 3:1）

【输出】
- 文件：docs/design/preview.html
- 另附 docs/design/README.md，列出设计决策（为什么选这个蓝、为什么字这么排）
  与该稿与现有实现的差异清单

【不要做】
- 不要改任何 .tsx/.py 文件
- 不要引入 Tailwind、不要 npm install
- 不要做深色模式
```

---

### 提示词 P1 —— 阶段 1：SQLite 适配 + 数据打包

```text
【任务】让后端同时支持 PostgreSQL（开发/构建期）与 SQLite（桌面发行版），
并把现有真实数据导出为随包分发的 SQLite 文件。

【不可破坏的约束】以下产品规则在任何改动中都必须保持：
1. Eligibility / ACS / Preference 三层严格分离，不得合并
2. ACS 是 0–100 的「录取安全指数」，**不是录取概率**，任何文案与注释都不得暗示概率
3. 资格结论 FAIL 的志愿不得进入正式推荐
4. 用户的 hard_exclude 永远不得被优化器突破
5. 组合优化与最终排序是两步，不得合并
6. 政策数字（含志愿数量上限）必须从 admission_cycles 读取，不得写死
7. 所有推荐必须锁定 profile_version / preference_version /
   dataset_snapshot_id / policy_version / model_version
8. 大模型不得覆盖 Rule Engine 的确定性结论

【现状事实】（已核实，可直接依据）
- 项目根：D:\山东高考，后端在 backend/
- 4 个 models.py 各自定义模块级常量：SCHEMA = "reference" / "admission" /
  "candidate" / "recommendation"，共 33 张表
- JSONB 共 11 个字段，分布在 admission(3) / candidate(5) / recommendation(3) 三个 models.py
- 代码中没有任何 JSONB 运算符 SQL（->> @> jsonb_*），JSON 只在 Python 侧读写
- CREATE SCHEMA 只出现在 alembic/versions/0001_schemas.py
- search_path 设置集中在 app/db/session.py::set_schemas()，用 SELECT set_config(...)
- app/config.py 用 pydantic-settings，字段 database_url
- 现有数据：院校 1,153；专业序列 25,280；招生计划 73,198；录取记录 72,885+18,074(2026)；
  一分一段 1,559 行；数据集快照 code = SD-2022-2026
- 开发环境：WSL2 PostgreSQL 16，DATABASE_URL 见 backend/.env

【任务清单】
1. 在 app/common/enums.py 或新模块中提供 schema 解析：
   - PostgreSQL 模式返回原 schema 名（"reference" 等）
   - SQLite 模式返回 None（表不分 schema）
   - 4 个 models.py 的 SCHEMA 常量改为调用它。**不要改表名与字段**
2. 新增双模式 JSON 类型（建议放 app/db/types.py）：
   JSONType = JSON().with_variant(JSONB, "postgresql")
   替换 11 个 mapped_column(JSONB) 为 mapped_column(JSONType)
3. app/config.py 增加：
   - db_mode: "postgres" | "sqlite"（默认 postgres，由环境变量 GAOKAO_DB_MODE 覆盖）
   - data_dir: 数据目录（SQLite 文件所在目录，默认取 %LOCALAPPDATA%）
4. app/db/session.py：
   - SQLite 模式：connect_args={"check_same_thread": False}，跳过 set_schemas
   - PostgreSQL 模式：行为完全不变
5. 新增 backend/app/db/bootstrap.py：
   - ensure_sqlite_schema(engine)：用 Base.metadata.create_all() 建表
     （桌面模式不走 Alembic）
   - 幂等：重复调用不报错
6. 新增构建脚本 scripts/export_sqlite.py：
   - 从 PostgreSQL（现有 DATABASE_URL）读取全部 33 张表，写入指定 .db 文件
   - 保留主键与外键关系的完整性；按依赖顺序写入
   - 写入后输出每张表的行数，供人工核对
   - 支持 --out 参数指定输出路径
7. 新增冒烟脚本 scripts/smoke_sqlite.py：
   - 用 SQLite 模式起一次完整推荐流程（建档案 → 建偏好 → 生成推荐 →
     生成方案 → 体检），全部通过内存或临时文件数据库
   - 输出每步耗时与关键计数（候选池规模、风险分布、方案条目数、健康分）
8. 补充测试 backend/tests/test_sqlite_mode.py：
   - 同一套模型在 SQLite 下能建表、能写入、能读回（含 JSON 字段）
   - 至少一个端到端断言：推荐流程在 SQLite 下产出的候选池非空

【验收标准】（全部必须通过，逐条贴出结果）
- cd backend; .\.venv\Scripts\python.exe -m ruff check app tests
- cd backend; .\.venv\Scripts\python.exe -m mypy app
- cd backend; $env:TEST_DATABASE_URL='postgresql+psycopg://gaokao:gaokao_dev_only@127.0.0.1:5432/gaokao_test'; .\.venv\Scripts\python.exe -m pytest -q
  （现有全部测试必须仍然通过，不允许为了新功能放宽任何断言）
- python scripts/export_sqlite.py --out build/gaokao.db 输出各表行数，与 PostgreSQL 侧一致
- python scripts/smoke_sqlite.py 三分钟内跑完，退出码 0
- PostgreSQL 模式下所有现有行为不变（E2E 52/52）

【不要做】
- 不要改任何业务算法（ACS 公式、规则引擎、优化器）
- 不要改 API 路径与响应结构
- 不要引入新的数据库依赖（不要 SQLAlchemy 之外的 ORM）
- 不要为了跑通 SQLite 而放宽测试断言或删除测试
- 不要动 frontend/
```

---

### 提示词 P2 —— 阶段 1：Electron 壳 + 打包 + 安装包

```text
【任务】把后端 Python 引擎与前端静态页面打包成 Windows 桌面应用，
产出可安装的 .exe 与免安装 zip。界面保持现状，本阶段只解决「能装、能开、断网能用」。

【不可破坏的约束】
（同 P1 的 8 条产品规则，原样保留）

【前置条件】
- P1 已完成：后端支持 SQLite 模式，scripts/export_sqlite.py 可产出 gaokao.db

【任务清单】
1. 前端改为静态导出
   - frontend/next.config.ts：桌面构建时 output: "export"，并设置
     trailingSlash: true（保证 file/静态路径一致）
   - 确认 7 个页面全部是客户端组件（现状已满足），layout.tsx 保留 server 组件
   - API 基址在桌面模式下必须是相对路径或运行时注入（不要编译期写死 127.0.0.1:8000）
     建议：用 NEXT_PUBLIC_API_BASE_URL="/api/v1" 让静态页面与 API 同源
   - 产出 frontend/out/
2. 后端静态托管
   - app/main.py 在桌面模式下挂载 StaticFiles：
     - "/" 指向打包进来的静态目录，支持 SPA 回退（未匹配路径返回 index.html）
     - API 仍在 /api/v1 下
   - 静态目录路径由配置提供，开发模式下不挂载（保持现有 next dev 工作流）
3. 引擎入口
   - 新增 backend/app/desktop.py（或 ops/desktop_main.py）：
     - 首次启动：若数据目录无 gaokao.db，从随包资源复制
     - 选一个空闲端口（bind 0 探测后释放，或让 uvicorn 用 port=0 后读回）
     - 启动 uvicorn，就绪后把实际端口写到 stdout 一行固定格式，供 Electron 解析
     - 收到父进程退出/收到 SIGTERM 时干净关闭
   - 所有日志写入数据目录 logs/engine.log，不要往控制台刷
4. PyInstaller 打包
   - 新增 backend/packaging/engine.spec
   - 只打运行时依赖：fastapi、uvicorn、sqlalchemy、psycopg(仅构建期，可排除)、
     ortools、pydantic、openpyxl
   - **排除** pandas、polars、xlrd、alembic、pytest（如果运行路径不 import 它们）
   - 产物：one-folder 模式（不要 one-file，启动更慢且杀软更敏感）
   - 把 frontend/out/ 与 gaokao.db 作为数据文件打进
5. Electron 主进程
   - 新增 desktop/ 目录：package.json、main.js、preload.js
   - 启动流程：显示启动画面窗口（含应用名 + "正在准备本地引擎…" + 进度点）
     → spawn 引擎 → 解析端口 → 主窗口 loadURL("http://127.0.0.1:<port>/")
     → 关闭启动画面
   - 引擎启动失败：显示错误窗口，提供「打开日志目录」按钮
   - 窗口关闭时结束引擎子进程（含异常退出兜底，避免僵尸进程）
   - 禁用菜单栏、禁用 devtools（生产）、设置最小窗口 1280x800、记住窗口大小
6. 安装包
   - 用 electron-builder 产出：
     - NSIS 安装程序（可选安装路径、创建桌面与开始菜单快捷方式、可卸载）
     - 免安装 zip（解压即用）
   - 应用名与窗口标题统一为「山东高考智能志愿」
   - 图标暂用占位（documents 里若无图标源，用一个纯色 + 文字「鲁」的 256x256 PNG）
7. 一键构建脚本
   - scripts/build_desktop.ps1：依次执行
     导出 SQLite → 前端静态导出 → PyInstaller 打包引擎 → electron-builder 打包
   - 任一步失败立即停止并打印失败步骤
8. 文档
   - 新增 docs/DESKTOP_BUILD.md：构建环境要求、每步命令、产物位置、
     常见问题（杀软误报、端口占用、日志在哪）

【验收标准】
- 在干净目录下运行 scripts/build_desktop.ps1，产出：
  - dist/山东高考智能志愿-Setup-x.y.z.exe
  - dist/山东高考智能志愿-x.y.z-win.zip
- 安装后双击：出现启动画面 → 20 秒内进主界面，**全程无命令行窗口**
- **断网**（拔网线/关 WiFi）后仍能完成：成绩与选科 → 偏好画像 → 推荐结果 →
  志愿编辑器 → 志愿体检
- 任务管理器里只有一个应用进程组（Electron + 引擎子进程），关闭窗口后引擎进程消失
- 重复安装/覆盖安装不丢数据（数据目录在 %LOCALAPPDATA%，不在安装目录）
- 现有 Web 开发流程不受影响：
  - backend 用 uvicorn 起在 8000，frontend 用 next dev 起在 3000，
  - cd backend; .\.venv\Scripts\python.exe ops\e2e_check.py http://127.0.0.1:8000 得到 52/52

【不要做】
- 不要改界面样式（本阶段维持现状，美化在阶段 2）
- 不要引入自动更新（阶段 3）
- 不要把数据目录放在 Program Files（需要管理员权限会导致写入失败）
- 不要打包测试数据目录或 .env 文件
```

---

### 提示词 P3 —— 阶段 2：设计系统落地（基础组件）

```text
【任务】把已确认的设计稿落地为前端设计系统与基础组件。
只改视觉层与组件，不改任何业务逻辑、不改数据流。

【前置条件】
- docs/design/preview.html 已经用户确认
- 阶段 1 已完成（桌面版可安装）

【不可破坏的约束】
1. ACS 是「录取安全指数」，不是录取概率 —— 任何新增文案都不得暗示概率
2. 5 个风险层的语义与颜色必须保持一一对应，不得因美化而让两层看起来相似
3. 不得删除或弱化现存的免责声明与「数据截至」提示
4. 不得改动任何 .py 文件
5. 现有 52 项 E2E 必须全部通过

【任务清单】
1. 重做 globals.css 的设计 token（按确认稿）：
   - 色板（含 5 个风险层）、字体阶梯、间距、圆角、阴影、焦点环
   - 保持 CSS 变量命名风格（--primary 等），减少组件层改动
2. 重写基础组件（frontend/src/components/ui/）：
   - button.tsx、card.tsx、badge.tsx、input.tsx、modal.tsx
   - 新增 select.tsx、tabs.tsx、tooltip.tsx（若现有页面已在用 Radix，则统一到 Radix）
   - 每个组件必须覆盖：默认/悬停/聚焦/禁用/加载 五态；键盘可达；
     焦点环清晰可见
3. 重做 app-nav.tsx：当前步骤高亮、步骤序号、窄屏折叠
4. 重做 badges.tsx：风险层徽标、资格徽标、ACS 显示（含"非概率"提示的呈现方式）
5. 数字排版规范：位次、ACS、分数、学费统一用等宽数字（font-variant-numeric: tabular-nums），
   并保证表格列对齐
6. 清除样式债：现有页面里散落的 inline className 变体收敛到组件 props
7. 视觉回归自查：截图对比改前/改后，确认没有字号过小、对比度不足、遮挡

【验收标准】
- cd frontend; npx tsc --noEmit 通过
- cd frontend; npx eslint src 通过
- cd frontend; npx next build 成功
- 浏览器走查 7 个页面无控制台错误
- cd backend; .\.venv\Scripts\python.exe ops\e2e_check.py http://127.0.0.1:8000 → 52/52
- 对比度实测：正文 ≥4.5:1，大字号 ≥3:1（在 README 中贴出实测值）

【不要做】
- 不要重排页面结构（那是 P4）
- 不要加图表或动效（那是 P5）
- 不要改任何交互逻辑与状态管理
```

---

### 提示词 P4 —— 阶段 2：关键页面重排 + 档案管理

```text
【任务】用 P3 落地的新设计系统重排关键页面，并新增「我的档案」列表页。
不改业务算法与 API 契约（可新增只读接口）。

【前置条件】
- P3 已完成，基础组件与设计 token 已生效

【不可破坏的约束】
（同 P3 的 5 条，另加）
6. 新增接口必须遵循现有分层：Router → Service → Repository → DB
7. 档案删除必须是软删除或级联安全：不得留下孤儿推荐会话/方案

【任务清单】
1. 档案管理
   - 新增页面 /profiles（我的档案）：列出所有 candidate_profiles，
     显示 姓名/位次/选科/创建时间/关联方案数
   - 支持：新建（跳转到成绩与选科）、重命名、删除（二次确认，说明会连带删除什么）
   - 记住上次打开的档案（localStorage 或 flow-state），启动时直接进入上次位置
   - 后端补充：重命名与删除接口（若不存在），删除需级联清理
     推荐会话/方案/版本，且不得影响其它档案
   - 迁移脚本或管理入口：清理当前库里 31 个验证档案（rank 60000 的历史测试数据），
     保留至少 1 个可供体检脚本使用
2. 首页重排：产品定位一句话 + 主行动按钮 + 数据说明（截至年月、
   数据来源质量、免责声明）
3. 推荐结果页重排：筛选栏收敛、卡片信息层级、分页、排序说明
4. 志愿编辑器页重排（**最高风险，注意**）：
   - 左候选池与右方案两栏在 1280 宽度下不挤压
   - 「列表 / 按学校」切换、两侧筛选栏、拖拽禁用提示保持可用
   - 不得破坏已有的拖拽与锁定逻辑
5. 志愿体检页重排：健康分呈现、问题分组（错误/警告/提示）、修复建议
6. 版本历史页轻量对齐
7. 统一的空状态、加载骨架、错误态（用 P3 的组件）

【验收标准】
- 全部 P3 验收项
- 档案页 CRUD 走查通过；删除档案后其推荐会话与方案确实消失，
  其它档案不受影响
- 编辑器页：拖拽排序、锁定、加入、删除、筛选禁用拖拽提示 全部回归通过
- 断网状态下的桌面版同样通过（若 P2 已完成）
- 1280x800 与 1440x900 两个分辨率下无横向滚动、无文字截断

【不要做】
- 不要改 ACS/规则/优化器/体检算法
- 不要改已有 API 的响应结构（可新增字段，不可改语义）
- 不要加图表（P5）
```

---

### 提示词 P5 —— 阶段 3：数据可视化与动效

```text
【任务】为关键数据增加可视化与克制的动效，提升可读性。
**手写 SVG，不引入图表库**（避免 ECharts/Recharts 的体积与样式冲突）。

【不可破坏的约束】
1. 图表不得暗示录取概率；ACS 相关图示一律标注「安全指数」
2. 视觉化不得掩盖真实数据：不得为了好看而截断坐标轴或省略异常值
3. 动效必须可被系统「减少动画」设置关闭（prefers-reduced-motion）
4. 任何动画不得延迟用户可操作时间

【任务清单】
1. 通用图表组件（手写 SVG，放在 frontend/src/components/charts/）：
   - BarDistribution：风险层分布（5 层，含数量与占比）
   - RankCurve：某专业序列的历年最低位次曲线（横轴年份，纵轴位次，
     位次越小越靠上；标出缺数据的年份为断点）
   - Gauge：健康分仪表（0–100，分段着色，标注当前分数）
   - 全部支持：悬停显示数值、键盘聚焦、空数据占位
2. 加载体验
   - 骨架屏替换现有"正在读取…"文案（列表、卡片、表格三种骨架）
   - 推荐生成中的进度反馈（当前阶段文案：资格判定 → 评分 → 优化 → 排序）
3. 微动效（克制，≤200ms）
   - 筛选栏展开/收起、卡片进入列表的淡入、风险层标签切换
   - 拖拽排序的占位与归还动画
   - 尊重 prefers-reduced-motion
4. 编辑器页增强
   - 两侧的实时计数与风险层缺口提示（当前 96 条目标 vs 各层实际）
   - 方案内风险层分布条

【验收标准】
- 全部 P4 验收项
- 图表在 0 条数据、1 条数据、上千条数据三种情况下都能正确渲染不崩
- 开启「减少动画」后无任何过渡动画
- 图表不引入新的 npm 依赖（package.json 依赖列表前后一致）
- 截图交付：每个图表一张改后截图

【不要做】
- 不要引入 ECharts / Recharts / Chart.js / D3
- 不要做深色模式
- 不要为了图表新增后端查询接口（现有数据已足够；确需新增需先说明理由）
```

---

### 提示词 P6 —— 阶段 3：导出与自动更新

```text
【任务】实现志愿方案的 PDF/Excel 导出，以及启动时的数据更新检查。

【不可破坏的约束】
1. 导出的文件必须标注：数据截至年月、ACS 是安全指数而非录取概率、
   数据来源质量（THIRD_PARTY）
2. 更新走**新数据集快照**，已有方案仍锁定旧 snapshot，不得被更新改写
3. 更新失败（断网/校验失败/磁盘满）必须静默降级，不得阻塞启动、不得损坏现有数据
4. 更新包必须校验（长度 + SHA-256）；校验失败一律丢弃

【任务清单】
1. Excel 导出
   - 后端接口：GET /api/v1/schemes/{id}/export.xlsx
   - 列：序号、院校、专业、专业组、选考要求、风险层、ACS、偏好分、学费、计划数、
     资格结论、锁定状态
   - 表头冻结、列宽自适应、数字列右对齐、风险层着色（与界面同色）
   - 第二张工作表：方案摘要（总条数、各风险层数量、健康分、生成时间、
     数据快照/模型版本）
   - 用 openpyxl，不引入新依赖
2. PDF 导出
   - 桌面模式：Electron `webContents.printToPDF`
   - 新增打印专用路由 /schemes/print（纯静态、无交互控件），
     页面内自带导出按钮触发 Electron 打印
   - 打印样式：A4 纵向、页边距、页眉含应用名与数据截至、页脚含页码
   - 首行说明：本表由「山东高考智能志愿」生成，ACS 为安全指数
3. 自动更新
   - 数据包格式：.gkdata（zip），内含 data.db（SQLite）、manifest.json
     （version、data_as_of、snapshot_code、sha256、size）、以及模型版本
   - 启动时（可关）：请求 GitHub Releases 的 latest 资产清单 →
     比较 manifest.version 与本地 → 有新版则提示用户 →
     下载到 update/ → 校验 → 解压到新目录 → 切换指针文件 → 提示重启生效
   - 设置项：是否自动检查更新（默认开）；「关于」页提供「检查更新」「导入数据包」
   - 手动导入：用户选一个 .gkdata 文件，走同样的校验与切换流程
   - 切换机制：数据目录下 current.txt 记录当前数据目录名，
     保留上一版以便回滚；切换失败自动回滚
4. 关于页
   - 应用版本、引擎版本、模型版本、数据快照 code、数据截至年月、
     数据库路径、日志目录（可打开）、更新检查开关

【验收标准】
- 导出的 xlsx 能被 Excel 打开，列与说明齐全；96 条方案全部在内
- 导出的 PDF 为 A4，中文字体正常（不出现方框），页码正确
- 模拟断网启动：不阻塞、不弹错误、正常进入主界面
- 用旧版本数据包导入：提示"已是最新"或正确拒绝
- 用篡改过 sha256 的数据包导入：被拒绝且现有数据不受影响
- 导入新版数据包后：旧方案的 snapshot 仍是旧的（查界面版本信息确认），
  新建的推荐才用新快照
- 全部既有 E2E 仍 52/52

【不要做】
- 不要做增量差分更新（整包替换即可）
- 不要联网上报任何使用数据
- 不要在用户未确认的情况下自动替换数据
```

---

## 九、总验收清单（每个阶段结束都要跑）

| 检查项 | 命令 | 期望 |
|---|---|---|
| 后端静态检查 | `cd backend; .\.venv\Scripts\python.exe -m ruff check app tests` | 通过 |
| 后端类型检查 | `cd backend; .\.venv\Scripts\python.exe -m mypy app` | 通过 |
| 后端全量测试 | `cd backend; $env:TEST_DATABASE_URL='...gaokao_test'; .\.venv\Scripts\python.exe -m pytest -q` | 全通过 |
| 端到端 API | `cd backend; .\.venv\Scripts\python.exe ops\e2e_check.py http://127.0.0.1:8000` | 52/52 |
| 前端类型检查 | `cd frontend; npx tsc --noEmit` | 通过 |
| 前端 lint | `cd frontend; npx eslint src` | 通过 |
| 前端构建 | `cd frontend; npx next build` | 成功 |
| 浏览器走查 | `node smoke_all_pages.mjs` | 7/7 |
| 完整流程 | `node walk_full_flow.mjs` | 5/5，体检分应为 83 左右 |
| 桌面冒烟 | 安装 package 后断网走完整流程 | 全通 |
| 免责声明 | 界面与导出文件中均出现 | 存在且未被弱化 |

**基线数值**（改动不应导致显著漂移，若漂移需说明原因）：
- 候选池 19,019 条；风险分布 高冲 4,931 / 冲 392 / 边界 215 / 稳 7,668 / 保 5,813
- 方案 96 条，健康分 83，0 错误 / 22 警告 / 43 提示

---

## 十、涉及文件清单（供执行时定位）

**后端**
- `app/common/enums.py` — `SCHEMAS` 常量
- `app/db/base.py` — MetaData / Base
- `app/db/session.py` — 引擎、`set_schemas`、`session_scope`
- `app/config.py` — `Settings.database_url`
- `app/main.py` — 路由挂载、CORS
- `app/{reference,admission,candidate,recommendation}/models.py` — 33 张表、`SCHEMA` 常量、11 个 JSONB
- `alembic/versions/0001_schemas.py` — 唯一的 `CREATE SCHEMA`
- `ops/e2e_check.py` — 52 项端到端检查

**前端**
- `next.config.ts` — 当前 `output: "standalone"`
- `src/app/globals.css` — 设计 token
- `src/components/ui/{button,card,badge,input,modal}.tsx`
- `src/components/{app-nav,badges,recommendation-card}.tsx`
- `src/lib/api/{client,endpoints,types}.ts`
- 7 个页面：`/`、`/onboarding/score`、`/onboarding/preferences`、
  `/recommendations`、`/schemes`、`/schemes/versions`、`/health-check`

**本轮新增**
- `docs/design/preview.html`、`docs/design/README.md`
- `docs/DESKTOP_BUILD.md`
- `desktop/`（Electron 主进程）
- `backend/packaging/engine.spec`
- `backend/app/db/{types.py,bootstrap.py}`、`backend/app/desktop.py`
- `scripts/{export_sqlite,smoke_sqlite,build_desktop}.ps1|py`
- `backend/tests/test_sqlite_mode.py`

---

## 十一、确认后我按这个顺序开工

1. **P0 设计稿** → 你确认风格
2. **P1 SQLite 适配** → 你确认 `smoke_sqlite.py` 能跑通
3. **P2 Electron 打包** → 你装上 exe、断网试一遍
4. **P3 设计系统落地** → 你看到新样式
5. **P4 页面重排 + 档案页** → 你确认交互
6. **P5 可视化动效** → 你确认观感
7. **P6 导出 + 自动更新** → 收尾

每个阶段结束我都会给你：改了哪些文件、跑了哪些验证、还剩什么问题。
