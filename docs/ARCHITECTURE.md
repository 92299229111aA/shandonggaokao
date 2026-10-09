# 架构说明

## 1. 总体形态：模块化单体

V1 是**一个** FastAPI 应用（`backend/app`），不拆微服务。分层固定为：

```
Router (app/api/v1/*)        只做 DTO 校验、调用 service、返回 DTO
  ↓
Service (app/*/service.py)   业务编排、事务边界（commit/rollback）、权限与scope校验
  ↓
Repository (app/*/repository.py)  唯一与 ORM Session 打交道的层
  ↓
Database (PostgreSQL, 四个 schema)
```

**硬性约束**

- Router 里不允许出现 ORM 查询（`select()` / `session.query()`）。
- Rule Engine / Scoring / Optimizer **不依赖 HTTP、不依赖 ORM Session**，只接收纯数据类，
  因此可以脱离数据库与网络独立单测。

## 2. 四个 schema 的职责

| Schema | 职责 | 主要表 |
|---|---|---|
| `reference` | 院校、校区、教育部专业标准库 | `universities` `campuses` `majors` |
| `admission` | 数据来源、周期、计划、历史录取、规则、快照 | `source_documents` `dataset_snapshots` `admission_cycles` `exam_year_stats` `score_rank_segments` `program_series` `subject_rule_sets/groups/items` `admission_plans` `admission_outcomes` `admission_requirements` |
| `candidate` | 考生档案、特殊条件、偏好版本 | `profiles` `subjects` `subject_scores` `attributes` `preference_profiles` `major_preferences` `location_preferences` `university_preferences` `other_preferences` |
| `recommendation` | 模型版本、推荐会话、评分、方案、体检 | `model_versions` `sessions` `recommendation_items` `schemes` `scheme_versions` `scheme_items` `health_checks` `health_check_issues` |

## 3. 三层严格分离（本项目的核心不变式）

| 层 | 回答的问题 | 取值 | 落在哪 |
|---|---|---|---|
| **Eligibility** | 这个志愿**能不能报** | `PASS` / `FAIL` / `UNKNOWN` | `app/rules/` |
| **ACS** | 从历史数据看**录取有多安全** | 0~100 + 风险桶 | `app/scoring/acs.py` |
| **Preference** | 考生**有多想去** | 0~100 | `app/scoring/preference.py` |

不可违反：

1. Preference 不能覆盖选科/体检等硬规则；ACS 不能替考生决定喜好。
2. `FAIL` 不进入正式推荐方案；`UNKNOWN` 可进候选池，但**不计入保底数量**。
3. 用户 `hard_exclude`（专业/地域/学校/学费/中外合作）在优化器中**永不被突破**。
4. 大模型（LLM）**不得覆盖** Rule Engine 的确定性结论。
5. 组合优化与最终排序是**两步**：先选集合，再排顺序。

## 4. 版本锁定与可重放

`recommendation.sessions` 在创建时锁定：

```
profile_version        preference_version     dataset_snapshot_id
policy_version         model_version          algorithm_config_version
```

任何历史推荐都能用这些版本号重放得到相同结果。规则：

- 推荐引擎**只能**使用 `dataset_snapshots.status = 'PUBLISHED'` 的快照。
- 偏好永不原地修改：新偏好 = `version + 1` 的新行，旧行 `is_active = false` 但**仍可读取**。
- 方案编辑永不覆盖：每次编辑/优化生成新的 `scheme_versions` 行，内容在 `scheme_items`。
- 方案编辑使用乐观锁：`schemes.lock_version` 不匹配时返回 **409 SCHEME_VERSION_CONFLICT**。

## 5. 政策数字的唯一来源

`admission_cycles` 表是政策参数的**唯一**来源，包括：

- `application_limit`（志愿数量上限，当前 96）
- `policy_version`
- `province_code` / `exam_category` / `batch_code` / `round_no` / `program_level`

前端与后端都不得写死 96。`PreferenceService` 会校验 `target_count <= cycle.application_limit`，
超限直接返回 `RULE_VIOLATION` 并带上真实上限。

V1 范围也在数据库层被强制：`admission_cycles` 上的 CHECK 约束只允许
`exam_category = 'SUMMER_REGULAR'` 与 `batch_code = 'REGULAR_BATCH_1'`。

## 6. 目录边界

```
backend/app/
├── api/v1/          Router：profiles / catalog / meta / recommendations / schemes / health-check
├── api/deps.py      依赖注入（请求级 Session）
├── common/          enums（含风险桶边界）、errors（统一错误格式）、schemas、repository 基类
├── db/              base（DeclarativeBase + 命名约定）、session（engine/factory）、registry（全部模型）
├── reference/       院校/校区/专业模型
├── admission/       招生域模型、repository、catalog service、DTO
├── candidate/       考生域模型、repository、service、DTO、score→rank 转换
├── rules/           Eligibility Rule Engine（纯函数）
├── scoring/         ACS / Confidence / Preference（纯函数）
├── recommendation/  推荐流水线编排、会话与评分持久化
├── optimizer/       CP-SAT 组合优化 + 最终排序（纯函数 + 求解器）
├── healthcheck/     11 项体检（只诊断，不修改方案）
└── ingestion/       RAW→Parser→Normalizer→Matcher→Validator→Snapshot + DEMO seed
```

## 7. 数据可信度与演示数据隔离

- 每条重要事实都可追溯到 `source_documents`（含 `checksum_sha256`、`archive_path`、`quality`）。
- `admission_plans` / `admission_outcomes` 必带 `snapshot_id`，并可选带 `source_document_id`。
- DEMO 数据三重标记：`is_demo = true`（计划/结果）、`source_documents.quality = 'DEMO'`、
  `dataset_snapshots.code` 以 `DEMO-` 开头；`/api/v1/meta/dataset` 会返回明确警告。
- 无法可靠解析的高校特殊要求**不得猜测**：`admission_requirements.is_deterministic = false`，
  Rule Engine 对其输出 `UNKNOWN`。

## 8. 迁移策略

Alembic 迁移**按阶段拆分**（见 README 的 revision 表），禁止单个 migration 创建全部内容。
`tests/test_migrations.py::test_migrations_match_orm_metadata` 使用 Alembic 自身的
`compare_metadata` 对比迁移产物与 ORM 元数据，任何漂移都会让 CI 失败。

枚举值用 `VARCHAR + CHECK` 而非 PostgreSQL 原生 enum：新增政策取值只需改 CHECK，
不需要 `ALTER TYPE`（后者无法与其它 DDL 同事务执行，且回退困难）。
