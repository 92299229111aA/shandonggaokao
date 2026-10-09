# 山东高考智能志愿系统 V1

面向**山东省夏季高考普通类考生**的志愿决策系统。系统在官方招生规则与招生计划数据的约束下，完成：
报考资格过滤（Eligibility）→ 历史录取安全指数（ACS）→ 个人偏好建模（Preference）→
候选池构建 → 最多 96 个志愿的 CP-SAT 组合优化 → 最终排序 → 志愿体检 → 版本化保存与导出。

> **边界声明**：系统输出的是**决策辅助方案，不是录取保证**。V1 未做概率校准，
> 因此全系统统一使用「录取安全指数 ACS + 数据可信度 Confidence」，
> **禁止**出现「录取概率 xx%」这类表述。

## V1 支持范围

| 维度 | 支持 |
|---|---|
| 省份 | 山东（37） |
| 考试类型 | 夏季高考 |
| 类别 | 普通类 |
| 批次 | 常规批 |
| 轮次 | 第 1 次志愿 |
| 层次 | 本科计划 |
| 志愿单位 | 专业（专业类）+ 学校 |
| 志愿数量 | 从 `admission.admission_cycles.application_limit` 动态读取（当前兼容最多 96 个） |

**明确不支持**：提前批、艺术类、体育类、春季高考、常规批第 2/3 次志愿、专科完整推荐、
代替考生在官方系统提交志愿。这些范围在数据库 CHECK 约束层面即被拒绝。

完整产品规格见 [`docs/handoff/spec.md`](docs/handoff/spec.md)（原始 DOCX：`docs/handoff/PROJECT_HANDOFF_AGENT_SPEC.docx`）。

## 技术栈

- **Frontend**：Next.js 15（App Router）+ TypeScript + TailwindCSS + shadcn/ui + TanStack Query + dnd-kit + Zod
- **Backend**：Python 3.12+ / FastAPI / Pydantic v2 / SQLAlchemy 2 / Alembic
- **Database**：PostgreSQL 16+（`reference` / `admission` / `candidate` / `recommendation` 四个 schema）
- **Cache**：Redis（仅缓存公共查询类数据）
- **Optimization**：Google OR-Tools CP-SAT
- **Data processing**：Polars 优先，Pandas 兼容
- **Dev deployment**：Docker Compose

架构与模块边界见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)，算法细节见 [`docs/ALGORITHM.md`](docs/ALGORITHM.md)，
技术决策记录见 [`docs/DECISIONS.md`](docs/DECISIONS.md)，已知限制见 [`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md)。

## 目录结构

```
.
├── backend/                 # FastAPI 模块化单体
│   ├── app/
│   │   ├── api/v1/          # Router 层（只做 DTO 校验与调用 service）
│   │   ├── reference/       # 院校/专业基础数据
│   │   ├── admission/       # 招生周期、计划、历史录取、规则
│   │   ├── candidate/       # 考生档案与偏好
│   │   ├── rules/           # Eligibility Rule Engine（纯函数，无 HTTP/ORM 依赖）
│   │   ├── scoring/         # ACS / Confidence / Preference（纯函数）
│   │   ├── recommendation/  # 推荐流水线编排 + Repository
│   │   ├── optimizer/       # CP-SAT 组合优化 + 最终排序
│   │   ├── healthcheck/     # 志愿体检（只诊断，不修改方案）
│   │   ├── ingestion/       # ETL：RAW→Parser→Normalizer→Matcher→Validator→Snapshot
│   │   ├── db/              # engine/session/base
│   │   └── common/          # 枚举、错误、分页等
│   ├── alembic/versions/    # 按阶段拆分的 migration
│   ├── tests/               # unit + integration
│   └── ops/                 # 容器入口、开发自检脚本
├── frontend/                # Next.js 六页面
│   └── src/{app,components,features,lib,types}
├── docs/                    # 规格、架构、算法、决策、限制
├── scripts/                 # 仓库级脚本
├── docker-compose.yml
└── .env.example
```

---

## 本地启动

### 方式 A：Docker Compose（推荐，一键起 PostgreSQL + Redis + API + Web）

```bash
cp .env.example .env          # 首次
docker compose up -d --build
```

启动后：

| 服务 | 地址 |
|---|---|
| 前端 | http://127.0.0.1:3000 |
| OpenAPI 文档 | http://127.0.0.1:8000/docs |
| API 健康检查 | http://127.0.0.1:8000/api/v1/meta/health |
| PostgreSQL | 127.0.0.1:5433 |
| Redis | 127.0.0.1:6380 |

API 容器默认自动执行 `alembic upgrade head` 与 demo 数据 seed（`AUTO_MIGRATE` / `AUTO_SEED`，仅开发用）。

> **内存要求**：Docker Desktop 的 Linux VM 至少需要 2 GB 可用内存。若宿主机内存紧张，
> 请使用下面的方式 B。

### 方式 B：宿主机运行（PostgreSQL 由你自己提供）

适用于无法运行 Docker 的环境。任何 PostgreSQL 16+ 实例都可以，包括 WSL2 内的原生安装：

```bash
# 在 WSL/Ubuntu 中（可选）
sudo apt-get install -y postgresql
sudo pg_ctlcluster 16 main start
sudo -u postgres psql -c "CREATE ROLE gaokao LOGIN PASSWORD 'gaokao_dev_only' SUPERUSER;"
sudo -u postgres createdb -O gaokao gaokao
sudo -u postgres createdb -O gaokao gaokao_test      # 集成测试用
```

后端：

```bash
cd backend
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                   # 按需修改 DATABASE_URL
alembic upgrade head
python -m app.ingestion.seed_demo                      # 生成 DEMO 数据
uvicorn app.main:app --reload --port 8000
```

前端：

```bash
cd frontend
pnpm install
cp .env.example .env.local
pnpm dev
```

---

## 数据库 Migration

```bash
cd backend
alembic upgrade head              # 升级到最新
alembic downgrade -1              # 回退一步
alembic current                   # 当前版本
alembic history --verbose         # 全部版本
```

Migration **按阶段拆分**（禁止单个 migration 创建全部内容）：

| Revision | 内容 |
|---|---|
| `0001_schemas` | 创建四个 schema 与扩展 |
| `0002_reference` | universities / campuses / majors |
| `0003_admission_sources` | source_documents / dataset_snapshots |
| `0004_admission_cycles` | admission_cycles / exam_year_stats / score_rank_segments |
| `0005_program_series` | program_series（跨年份稳定标识） |
| `0006_admission_rules_plans` | 选科规则 DNF、admission_plans、admission_outcomes、admission_requirements |
| `0007_candidate` | profiles / subjects / attributes / preference_* |
| `0008_recommendation` | model_versions / sessions / recommendation_items / schemes / health_checks |

一致性保障：`tests/test_migrations.py::test_migrations_match_orm_metadata` 使用 Alembic 自身的
`compare_metadata` 对比「迁移后的真实库」与 ORM 元数据，**任何漂移都会让测试失败**。

## Seed（演示数据）

```bash
cd backend
python -m app.ingestion.seed_demo              # 幂等：重复执行不会产生重复数据
python -m app.ingestion.seed_demo --reset      # 重建（有档案/方案时会拒绝并说明原因）
python -m app.ingestion.seed_demo --reset --force   # 连同派生数据一起删除后重建
```

生成的 DEMO 数据集：**31 个省级行政区 × 3 个档位 = 93 所院校、191 条招生计划、
2023–2025 三年共 573 条录取结果、96 条一分一段记录**，并特意包含
「计划从 50 骤减到 5」「只招 5 人」「中外合作+面试」「英语单科≥115」「不招色盲色弱」
等边界样本，用于覆盖 UNKNOWN / FAIL / 计划缩减等分支。

院校名称为**明示的占位名称**（「演示大学（江苏省·1档）」），代码前缀 `DEMO`。
所有演示数据均标记 `is_demo = true`，`source_documents.quality = 'DEMO'`，
`dataset_snapshots.code` 为 `DEMO-SD-2026`。它们**不是**官方数据，
只用于功能开发与回归测试。真实数据走下面的上传接口。

## 导入真实招生数据

```bash
API=http://127.0.0.1:8000/api/v1

# 三个接口，默认 dry_run=true（只校验、不写库）
curl -X POST "$API/ingestion/score-rank?dry_run=false" -F "file=@score_rank.csv" \
     -F "publisher=山东省教育招生考试院"
curl -X POST "$API/ingestion/plans?dry_run=false"     -F "file=@plans.csv"    -F "cycle_id=1" -F "snapshot_id=1"
curl -X POST "$API/ingestion/outcomes?dry_run=false"  -F "file=@outcomes.csv" -F "snapshot_id=1"

curl "$API/ingestion/templates"     # 取 CSV 表头模板
```

- **有一条错误就整个文件不写入**，不存在"导一半"的状态，错误报告带源文件行号
- 列名兼容中英文别名，编码自动识别 UTF-8 / GBK，分隔符自动嗅探
- 幂等：同一份文件重复上传只更新不重复；改文件名也认得出来（按内容哈希）
- 每次导入写入 `source_documents`（含 SHA-256、发布单位、来源 URL），
  标记 `quality='OFFICIAL'`、`is_demo=False`

**完整的字段规格、导入顺序、错误码表与导入后检查清单见
[`docs/DATA_IMPORT.md`](docs/DATA_IMPORT.md)。**

> **为什么仓库里没有真实数据**：招生计划数与录取位次是官方发布的事实。为真实院校
> 编造这些数字，会产出一份看起来权威但实际错误的方案，而考生可能据此做真实填报
> 决策 —— 这比没有数据危险。演示数据因此使用占位名称但覆盖全国 31 个省级行政区，
> 以便真实验证地域偏好维度。

## 端到端验收

后端提供两个脚本，前者自检规则引擎装配，后者打真实 HTTP 接口跑完整个六步流程：

```bash
cd backend
python ops/check_models.py                      # ORM 模型与 schema 覆盖自检
python ops/check_rules.py                       # Rule Engine 装配自检

# 启动一个实例后用另一个终端跑端到端验收（52 项断言）
uvicorn app.main:app --port 8000 &
python ops/e2e_check.py http://127.0.0.1:8000
```

`ops/e2e_check.py` 覆盖：健康检查、政策上限读数、分数→位次换算、建档、偏好、
生成推荐（校验五个版本号全部锁定）、推荐卡片字段、hard_exclude 不泄漏、FAIL 不入池、
生成方案、顺序符合"意愿优先"、乐观锁 409、历史版本可读、锁定志愿在重新优化后保留、
体检 11 项、体检不修改方案、导出声明。最近一次运行结果见 [`docs/e2e-report.txt`](docs/e2e-report.txt)。

## 测试与静态检查

```bash
cd backend
export TEST_DATABASE_URL=postgresql+psycopg://gaokao:gaokao_dev_only@127.0.0.1:5432/gaokao_test
pytest                       # 全部测试（无数据库时集成测试自动 skip）
pytest -m "not integration"  # 仅纯算法测试，不需要数据库
ruff check app alembic tests ops
mypy app
```

```bash
cd frontend
pnpm typecheck               # tsc --noEmit
pnpm lint                    # eslint（flat config）
pnpm build                   # next build
```

> **注意**：`next build` 不执行 lint（`eslint.ignoreDuringBuilds = true`），因为 Next 15 内置的
> ESLint 运行器仍会传入 ESLint 9 已移除的 `useEslintrc`/`extensions` 选项。lint 由独立的
> `pnpm lint` 步骤负责，CI 与 README 都调用它。原因记录在 `docs/DECISIONS.md`。

> Rule Engine、Scoring、Optimizer、HealthCheck 全部是纯函数/纯数据类，**不依赖 HTTP 与 ORM Session**，
> 因此可以脱离数据库单独测试（见 `backend/tests/` 中不带 `integration` 标记的用例）。

## 开发流程

1. 先读 `docs/handoff/spec.md` 与 `docs/ARCHITECTURE.md`，确认改动落在哪一层。
2. 数据模型变更：改 `app/**/models.py` → `alembic revision -m "..."` 手写迁移 → 跑 `pytest`，
   `test_migrations_match_orm_metadata` 会强制模型与迁移一致。
3. 算法变更：同时更新 `docs/ALGORITHM.md` 与 `MODEL_PARAMETERS` 常量，并新增/更新单元测试。
4. 偏离规格的技术决策必须记入 `docs/DECISIONS.md`（原因、替代方案、影响）。
5. 每个阶段结束必须真实运行 `pytest` + `ruff` + `mypy`（后端）与 `pnpm typecheck` + `pnpm lint`（前端），
   失败即修复，不允许"应该能跑"。

## 安全

- 不提交任何密钥。所有配置项见 [`.env.example`](.env.example)；`.env` / `.env.local` 已在 `.gitignore` 中。
- 演示数据与真实数据通过 `is_demo` / `quality='DEMO'` / `DEMO-` 前缀严格区分。
