# 现状审计与分阶段实施计划

> 本文档是阶段 0 的交付物：仓库现状审计、环境约束、阶段划分与验收方式。
> 每个阶段完成后在此更新「状态」列，并附上真实验证命令与结果。

## 1. 仓库现状审计（阶段 0 开始时）

| 项目 | 审计结果 |
|---|---|
| 仓库路径 | `D:\山东高考` |
| 初始内容 | **空目录**（无任何文件、无 git 仓库、无依赖清单） |
| 已有依赖 | 无 |
| 可复用代码 | 无 |
| 冲突 | 无（不存在既有技术栈，因此不存在与规格冲突的实现） |

结论：按规格「若仓库为空，初始化 monorepo」执行，无历史包袱。

## 2. 交付环境审计（决定「如何真实验证」）

| 工具 | 实测结果 | 影响 |
|---|---|---|
| Python | 3.12.14（uv 管理）+ 3.13.12（系统） | 满足 `3.12+`，用 3.12 建 venv |
| Node.js | v24.18.0 | 满足 Next.js 15 |
| pnpm | 11.7.0 | 前端包管理 |
| Docker CLI / Compose | 29.6.2 / v5.3.1 已安装 | — |
| Docker Daemon | **无法启动**：`engine linux/hyperv failed to start: 系统内存不足，无法启动虚拟机 DockerDesktopVM，需要内存 2048 MB` | 见下 |
| 宿主机内存 | 总计 7.4 GB，空闲约 0.8–2.7 GB | Docker Desktop 的 Linux VM 起不来 |
| WSL2 | Ubuntu 24.04.1（4 vCPU，2.3 GB 可用）可用 | **用 WSL2 原生 PostgreSQL 16.15 作为验证库** |
| PyPI / npm registry | 均可达 | 依赖可安装 |

### 关键约束与应对

**约束**：本机内存不足以运行 Docker Desktop 的 Linux 虚拟机，因此
`docker compose up` 在本机**无法实测**。

**应对**（符合规格"任何与规格冲突的实现以规格为准；确有技术原因需变更时在 docs/DECISIONS.md 写明"）：

1. `docker-compose.yml` 仍按规格完整交付（PostgreSQL 16 + Redis 7 + API + Web，含 healthcheck、
   依赖顺序、卷挂载），并在 `docs/KNOWN_LIMITATIONS.md` 明确标注「本机未实测，需 ≥2 GB 可用内存的环境验证」。
2. 数据库层验收改为在 **WSL2 Ubuntu 24.04 的原生 PostgreSQL 16.15** 上真实执行：
   `alembic upgrade head`、`downgrade`/`upgrade` 往返、约束与索引断言、集成测试。
   连接方式与 Docker 完全一致（`postgresql+psycopg://gaokao:...@127.0.0.1:5432/gaokao`），
   因此**迁移脚本与 ORM 的正确性是被真实验证过的**，不是"应该可以"。
3. 端口约定：Docker 发行端口为 5433/6380（避免与其它项目冲突），宿主/ WSL 原生安装用 5432。
   `DATABASE_URL` 完全由环境变量驱动，两种方式都不需要改代码。

## 3. 分阶段实施计划

| 阶段 | 内容 | 验收方式 | 状态 |
|---|---|---|---|
| **0** | monorepo、docker-compose、.env.example、README、docs、lint/test 基线 | 目录结构、依赖可安装、README 可复现 | ✅ 完成 |
| **1** | 四 schema、全部核心表、约束/索引、SQLAlchemy 2 typed models、**分阶段 Alembic** | `alembic upgrade head`（真实库）+ downgrade/upgrade 往返 + 模型/迁移一致性测试 + 约束测试 | ✅ 完成 |
| **2** | Profiles / Attributes / Preferences / Catalog / Meta API（DTO、统一错误、OpenAPI） | API 集成测试（含 score→rank 转换、偏好版本化） | ✅ 完成 |
| **3** | Eligibility Rule Engine（选科 DNF、总分、单科、语种、性别、体检、资格） | PASS/FAIL/UNKNOWN 单元测试，含组合规则与 UNKNOWN 不猜 | ✅ 完成 |
| **4** | ACS / Confidence / Preference 评分 | 公式边界测试、1 年/3 年历史、计划骤降、强波动、缺失维度重归一 | ✅ 完成 |
| **5** | Recommendation Pipeline（锁定快照/版本 → 过滤 → 评分 → 分层候选池 → 持久化） | 同输入同版本可重放（determinism）测试 | ✅ 完成 |
| **6** | CP-SAT 组合优化 + Scheme 版本化 + 最终排序 | hard_exclude 不泄漏、locked 不移位、候选不足返回真实数量、409 乐观锁 | ✅ 完成 |
| **7** | Health Check 11 项检查 | 每项 check 单元测试 + 综合方案测试，确认不修改方案 | ✅ 完成 |
| **8** | Next.js 六页面串联（首页/成绩选科/偏好画像/推荐结果/编辑器/体检） | `pnpm typecheck` + `pnpm lint` + `pnpm build` + 与真实 API 联调 | ✅ 完成 |
| **9** | ETL 框架 + DEMO 数据集 + 端到端最终验收 | 快照可发布、来源可追溯、端到端跑通 | ✅ 完成 |

## 4. 阶段 0 实际创建的文件

```
.env.example  .gitignore  README.md  docker-compose.yml
backend/Dockerfile  backend/.env.example  backend/pyproject.toml
backend/requirements.txt  backend/requirements-dev.txt
backend/app/config.py
backend/ops/docker-entrypoint.sh  backend/ops/check_models.py
backend/ops/check_rules.py  backend/ops/e2e_check.py
frontend/Dockerfile  frontend/package.json  frontend/pnpm-lock.yaml
frontend/tsconfig.json  frontend/next.config.ts  frontend/tailwind.config.ts
frontend/postcss.config.mjs  frontend/components.json  frontend/eslint.config.cjs
frontend/.env.example
docs/handoff/spec.md  docs/handoff/PROJECT_HANDOFF_AGENT_SPEC.docx
docs/PHASE_PLAN.md  docs/ARCHITECTURE.md  docs/ALGORITHM.md
docs/DECISIONS.md  docs/KNOWN_LIMITATIONS.md  docs/e2e-report.txt
scripts/docx_to_md.py
```

## 5. 最终交付统计

| 项目 | 数量 |
|---|---|
| 后端 Python 源码 | ~10,600 行（`app/`） |
| 后端测试 | **302 个测试**（1 skip） |
| Alembic 迁移 | 8 个阶段化 revision |
| 数据库表 | 32 张，分布在 4 个 schema |
| API 路由 | 34 个（profiles / catalog / meta / recommendations / schemes / health-check / ingestion） |
| 前端页面 | 8 个路由（六步流程 + 历史版本 + not-found） |
| 演示数据 | 31 省 × 3 档 = 93 校 / 191 计划 / 573 条录取 |
| 端到端验收 | `ops/e2e_check.py` 52 项断言全通过 |

### 真实数据接入通道

| 数据 | 通道 | 状态 |
|---|---|---|
| 一分一段表 | `POST /api/v1/ingestion/score-rank` | ✅ 已实测 |
| 当年招生计划 | `POST /api/v1/ingestion/plans` | ✅ 已实测 |
| 历年投档结果 | `POST /api/v1/ingestion/outcomes` | ✅ 已实测 |
| 院校基础数据 | 直写 `reference.universities` | ⚠️ 无上传接口 |
| 专业目录 | 直写 `reference.majors` | ⚠️ 无上传接口 |
| 选科要求结构化 | 仅存原始文本 | ⚠️ 未解析成 DNF，按 UNKNOWN 处理 |

字段规格、错误码表与检查清单见 [`DATA_IMPORT.md`](DATA_IMPORT.md)。

### 待用户决策

**ACS 可达上限与风险桶边界冲突**：`d` 截断 ±0.30 叠加 Confidence 收缩后，ACS 实际
上限约 **77.6**，而 SAFE 桶定义为 ≥80，默认风险组合又要求「稳健 20 个 ACS≥80」。
推导见 [`DATA_IMPORT.md`](DATA_IMPORT.md) 第六节，验证脚本
`backend/ops/check_acs_ceiling.py`，决策记录见 [`DECISIONS.md`](DECISIONS.md) D-013。

### 阶段验收命令（可复现）

```bash
# 后端
cd backend
export TEST_DATABASE_URL=postgresql+psycopg://gaokao:gaokao_dev_only@127.0.0.1:5432/gaokao_test
pytest                      # 302 passed, 1 skipped
ruff check app alembic tests ops   # All checks passed
mypy app                    # no issues in 71 files
DATABASE_URL=postgresql+psycopg://gaokao:gaokao_dev_only@127.0.0.1:5432/gaokao alembic upgrade head
DATABASE_URL=... python -m app.ingestion.seed_demo
uvicorn app.main:app --port 8000 &
python ops/e2e_check.py http://127.0.0.1:8000     # 52/52 通过

# 前端
cd frontend && pnpm typecheck && pnpm lint && pnpm build
```
