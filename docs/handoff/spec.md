山东高考智能志愿系统 V1

项目交接说明书 · 产品规格 · 技术架构 · 智能体提示词

| 适用场景 将本项目整体交给 Codex、Claude Code、Cursor Agent、OpenAI Work/Agent 或其他具备代码仓库操作能力的智能体进行开发。文档既可整份作为上下文，也可按阶段拆分提示词执行。 |
|---|

| 项目项 | 内容 |
|---|---|
| 项目名称 | 山东高考智能志愿系统（山东考生版） |
| 当前版本 | V1 |
| 首版范围 | 山东夏季高考普通类常规批第1次志愿，本科计划 |
| 核心目标 | 根据位次、规则、偏好和历史招生数据生成可解释、可编辑、可体检的最多96个志愿方案 |
| 技术主栈 | Next.js + TypeScript + Tailwind/shadcn/ui + FastAPI + PostgreSQL + Redis + OR-Tools |
| 算法原则 | Eligibility / ACS / Preference 三层分离；组合优化与最终排序分离；不伪造录取概率 |

文档版本：1.0
建议保存至仓库：docs/PROJECT_HANDOFF_AGENT_SPEC.docx

## 目录

1. 项目目标与 V1 边界

2. 产品页面与用户流程

3. 核心业务规则

4. 推荐计算流水线

5. ACS / Confidence / Preference 公式

6. 96志愿组合优化与排序

7. PostgreSQL 数据模型

8. FastAPI / OpenAPI 设计

9. 前端结构与交互要求

10. 数据导入与版本管理

11. 工程目录与模块边界

12. 开发阶段与验收标准

13. 智能体总提示词

14. 分阶段 Agent Prompt

15. 测试与禁止事项

16. 最终交付清单

## 1. 项目目标与 V1 边界

本项目不是“输入分数查大学”的简单查询工具，而是面向山东考生的志愿决策系统。系统应在官方招生规则和招生计划数据约束下，完成报考资格过滤、录取风险分析、个人偏好建模、候选志愿构建、最多96个志愿组合优化、最终排序和志愿体检。

### 1.1 V1 支持范围

- 省份：山东。

- 考试类型：夏季高考。

- 类别：普通类。

- 批次：常规批。

- 轮次：第1次志愿。

- 教育层次：本科计划。

- 志愿单位：“专业（专业类）+学校”。

- 志愿数量：从 admission_cycle.application_limit 动态读取；当前设计兼容最多96个。

### 1.2 V1 暂不支持

- 提前批

- 艺术类

- 体育类

- 春季高考

- 常规批第2/3次志愿

- 专科完整推荐

- 自动代替考生在官方系统提交志愿

| 核心边界 系统输出的是“决策辅助方案”，不是录取保证。V1 禁止使用未经概率校准的“录取概率 xx%”宣传，统一使用“录取安全指数 ACS + 数据可信度”。 |
|---|

### 1.3 产品核心价值

- 规则可靠：先判断“能不能报”。

- 风险可解释：给出 ACS 和历史依据，而不是黑盒概率。

- 偏好真实：学校、专业、城市、费用、未来方向分开建模。

- 排序正确：最终顺序首先反映考生真实意愿。

- 方案可回溯：所有推荐保存数据快照、模型版本、偏好版本。

## 2. 产品页面与用户流程

| 首页   ↓ 输入成绩与选科   ↓ 建立考生画像 / 特殊条件   ↓ 选择专业、地域、学校、费用、风险偏好   ↓ 智能推荐候选池   ↓ 自动生成最多96个志愿   ↓ 96志愿编辑器   ↓ 志愿体检   ↓ 确认 / 导出 Excel、CSV |
|---|

### 2.1 页面列表

| 页面 | 核心功能 | 必须输出 |
|---|---|---|
| 首页 | 输入分数/位次入口；显示数据年份 | 当前数据版本、更新时间、开始填报按钮 |
| 成绩与选科 | 录入成绩、位次、选科、外语等 | CandidateProfile |
| 偏好画像 | 专业、地域、学校、费用、未来规划、风险偏好 | PreferenceProfile |
| 推荐结果 | 展示候选志愿池和风险层级 | ACS、Confidence、Preference、历史数据 |
| 96志愿编辑器 | 拖拽、增删、锁定、重新优化 | Scheme + SchemeVersion |
| 志愿体检 | 资格、风险、排序、数据更新检查 | HealthCheck + Issues |
| 导出 | 导出最终方案 | Excel/CSV，后续可增加PDF |

## 3. 核心业务规则

### 3.1 三套系统必须分离

| 模块 | 回答的问题 | 输出 |
|---|---|---|
| Eligibility | 这个专业是否允许考生填报？ | PASS / FAIL / UNKNOWN |
| ACS | 从历史位次与招生变化看，够到它有多安全？ | 0~100 + RiskBucket |
| Preference | 如果能上，考生有多想去？ | 0~100 |

| 禁止混用 Preference 不能覆盖选科/体检规则；ACS 不能替用户决定喜好；大模型不能直接改写 Rule Engine 的判断结果。 |
|---|

### 3.2 Eligibility 三态逻辑

| 任一 BLOCK 规则 FAIL     → FAIL 没有 FAIL，但存在 UNKNOWN → UNKNOWN 全部明确满足               → PASS |
|---|

- FAIL：不得进入正式推荐方案。

- UNKNOWN：可以出现在候选池，但必须标记风险，且不能被计入“保底数量”。

- PASS：正常进入后续评分。

### 3.3 硬条件与软偏好

| 类型 | 示例 | 处理 |
|---|---|---|
| 硬规则 | 选科不符、明确体检不符、单科要求不符 | 直接 FAIL |
| 用户硬排除 | 绝不接受某专业、超最高学费、不接受中外合作 | 直接从候选池排除 |
| 软偏好 | 更喜欢上海、偏好计算机、985优先 | 进入 Preference Score |

## 4. 推荐计算流水线

| [0] 锁定 Dataset Snapshot / Policy / Model Version [1] 标准化 CandidateProfile [2] Rule Engine 资格过滤 [3] User Hard Exclude 过滤 [4] 跨年份 Program Series 匹配 [5] 历史位次百分位标准化 [6] 计算 Base ACS [7] 计算 Data Confidence [8] 得到最终 ACS [9] 计算 Preference Score [10] 构建分层 Candidate Pool [11] CP-SAT 组合优化 [12] 按真实意愿排序 [13] Health Check [14] 保存 Scheme Version / 导出 |
|---|

### 4.1 每次推荐必须锁定的版本

- profile_version

- preference_version

- dataset_snapshot_id

- policy_version

- model_version

- algorithm_config_version（如果单独维护）

目标：任何历史推荐结果都可重放。智能体不得通过覆盖旧记录的方式“更新历史推荐”。

## 5. ACS / Confidence / Preference 公式

### 5.1 位次百分位标准化

不同年份考生规模变化，因此风险模型不能只比较裸位次。定义某年最低录取位次 r_y、当年排名人数 N_y：

| q_y = r_y / N_y q_c = candidate_rank / current_ranked_candidates d_y = clip( ln(q_y / q_c), -0.30, 0.30 ) |
|---|

d_y > 0 表示当前考生相对历史最低录取位置更有优势；d_y < 0 表示偏冲。

### 5.2 年份时间衰减

| w_y = exp(-lambda * age_y) lambda = ln(2) / 1.5 W_y = w_y / sum(w) M = sum(W_y * d_y) |
|---|

### 5.3 历史基础 ACS

| HistoryACS = 50 + 35 * tanh(M / 0.07) |
|---|

### 5.4 招生计划调整

| P = ln((plan_current + 1) / (median(plan_history) + 1)) PlanAdj = 7 * tanh(P / 0.40) |
|---|

### 5.5 趋势调整

对 ln(q_y) 按年份做加权线性回归，斜率记为 T。T < 0 表示近年来录取位次越来越靠前、竞争增强。

| TrendAdj = 6 * tanh(T / 0.04) BaseACS = clamp(HistoryACS + PlanAdj + TrendAdj, 0, 100) |
|---|

### 5.6 数据可信度 Confidence

| 项 | 权重 | 说明 |
|---|---|---|
| 历史覆盖 Ch | 0.25 | 3年及以上趋近1 |
| 新鲜度 Cr | 0.15 | 最近年份完整度 |
| 专业序列匹配 Cs | 0.20 | 跨年份专业是否真正可比 |
| 数据源 Csource | 0.15 | 官方来源优先 |
| 波动稳定 Cv | 0.15 | 历史位次波动越大越低 |
| 招生人数 Cp | 0.10 | 小样本降低可信度 |

| C = 0.25*Ch + 0.15*Cr + 0.20*Cs + 0.15*Csource + 0.15*Cv + 0.10*Cp ACS = 50 + (BaseACS - 50) * C |
|---|

| ACS | 风险层 |
|---|---|
| 0~29 | HIGH_REACH 高冲 |
| 30~44 | REACH 冲 |
| 45~59 | BORDER 边界 |
| 60~79 | STABLE 稳 |
| 80~100 | SAFE 保 |

### 5.7 Preference Score

| 维度 | 默认权重 |
|---|---|
| 专业匹配 | 35% |
| 学校匹配 | 15% |
| 地域匹配 | 15% |
| 升学/学校特征 | 10% |
| 职业方向 | 10% |
| 学费 | 10% |
| 个性化偏好 | 5% |

| Preference = sum(w_i * score_i) / sum(w_i for available dimensions) |
|---|

缺失维度必须从分母剔除，不得当作 0 分。专业、城市等如果标记 hard_exclude，则在评分前直接排除。

## 6. 96志愿组合优化与排序

### 6.1 Candidate Pool 分层截断

| 风险层 | 候选池上限 |
|---|---|
| HIGH_REACH | 120 |
| REACH | 120 |
| BORDER | 120 |
| STABLE | 150 |
| SAFE | 150 |

每层优先按 Preference Score DESC、Confidence DESC 截取。用户手动锁定的志愿必须无条件加入候选池。

### 6.2 Portfolio Optimizer

使用 Google OR-Tools CP-SAT。每个候选志愿 i 定义二元变量 x_i∈{0,1}。志愿总数上限来自 admission_cycles.application_limit。

| MAXIMIZE   PreferenceUtility + ConfidenceBonus - RiskBucketDeviation - UniversityConcentrationPenalty - MajorConcentrationPenalty |
|---|

### 6.3 默认风险组合

| 模式 | 高冲 | 冲 | 边界 | 稳 | 保 | 最低 ACS≥60 | 最低 ACS≥80 |
|---|---|---|---|---|---|---|---|
| 稳健 | 5 | 10 | 15 | 36 | 30 | 56 | 20 |
| 均衡 | 8 | 17 | 20 | 31 | 20 | 45 | 12 |
| 激进 | 15 | 25 | 20 | 24 | 12 | 32 | 8 |

| 重要 风险桶目标是软约束，最低安全数量可作为硬约束。不能为了凑满96个而加入用户明确不接受的专业。若只有78个真正可接受志愿，系统应返回78个并提示放宽条件。 |
|---|

### 6.4 最终排序

| 1. 用户手动固定位置 2. desire_band DESC 3. ACS ASC 4. Preference Score DESC 5. Confidence DESC  desire_band = floor(PreferenceScore / 3) |
|---|

解释：最终志愿顺序首先代表“考生真正更想去哪里”。在喜好近似的同一 desire_band 中，更难的志愿放前面。

## 7. PostgreSQL 数据模型

### 7.1 Schema

| Schema | 职责 |
|---|---|
| reference | 院校、校区、教育部专业标准库 |
| admission | 招生周期、招生计划、录取结果、规则、数据源、快照 |
| candidate | 考生档案、特殊条件、偏好版本 |
| recommendation | 模型版本、推荐会话、评分结果、志愿方案、体检 |

### 7.2 关键表关系

| reference.universities     ├─ reference.campuses     └─ admission.program_series ─ reference.majors                  ↓         admission.admission_plans           ├─ subject_rule_sets           ├─ admission_requirements           └─ admission_outcomes  candidate.profiles     ├─ candidate.subjects     ├─ candidate.attributes     └─ candidate.preference_profiles                  ↓ recommendation.sessions                  ↓ recommendation.items                  ↓ recommendation.schemes                  ↓ recommendation.scheme_versions                  ↓ recommendation.scheme_items                  ↓ recommendation.health_checks |
|---|

### 7.3 关键字段定义

| 表 | 关键字段 |
|---|---|
| admission_cycles | province_code, exam_year, exam_category, batch_code, round_no, application_limit, policy_version |
| exam_year_stats | exam_year, ranked_candidates |
| score_rank_segments | score, same_score_count, cumulative_rank |
| program_series | university_id, major_id, campus_id, canonical_name, series_key, cooperative |
| admission_plans | cycle_id, series_id, plan_count, tuition, subject_rule_set_id, series_match_quality, snapshot_id |
| admission_outcomes | min_score, min_rank, avg_rank, max_rank, admitted_count |
| admission_requirements | requirement_type, operator, operand JSONB, severity |
| candidate.profiles | exam_year, score, rank, subjects关联, version |
| preference_profiles | risk_mode, tuition limits, private/cooperative preferences, version |
| recommendation.items | eligibility, base_acs, confidence, acs, risk_bucket, preference dimensions |
| scheme_items | scheme_id, version_no, position, recommendation_item_id, locked |

### 7.4 推荐的 Migration 顺序

| 0001_create_schemas_extensions 0002_create_enums 0003_reference_universities 0004_reference_majors 0005_admission_sources_snapshots 0006_admission_cycles_exam_stats 0007_admission_program_series 0008_admission_subject_rules 0009_admission_plans 0010_admission_outcomes_requirements 0011_candidate_profiles 0012_candidate_preferences 0013_recommendation_models_sessions 0014_recommendation_items 0015_recommendation_schemes 0016_recommendation_health_checks 0017_indexes_constraints |
|---|

### 7.5 数据完整性要求

- 推荐历史不可覆盖，必须版本化。

- 推荐引擎只能使用 PUBLISHED snapshot。

- source_documents 保存每个重要事实来源。

- admission_plans 和历史 outcome 必须可追溯到 source_document_id。

- 敏感考生属性非必要不采集；如果采集，仅用于规则判断并做好最小化存储。

## 8. FastAPI / OpenAPI 设计

### 8.1 API 分组

| /api/v1/profiles /api/v1/profiles/{id}/attributes /api/v1/profiles/{id}/preferences /api/v1/catalog/universities /api/v1/catalog/majors /api/v1/catalog/plans/{plan_id} /api/v1/recommendations /api/v1/recommendations/{session_id}/items /api/v1/schemes /api/v1/schemes/{scheme_id} /api/v1/schemes/{scheme_id}/order /api/v1/schemes/{scheme_id}/optimize /api/v1/schemes/{scheme_id}/health-check /api/v1/meta/dataset /api/v1/meta/admission-cycles |
|---|

### 8.2 DTO 约束

- ProfileCreate：score 与 rank 至少有一个；subjects 必填。

- PreferenceCreate：risk_mode、学费、民办/中外合作开关、专业/地域/学校偏好。

- RecommendationCreate：profile_id + preference_id + cycle_id。

- SchemeCreate：recommendation_session_id + risk_mode + target_count。

- SchemeOrderUpdate：携带 version 做 optimistic locking。

- HealthCheckRequest：指定 scheme version。

### 8.3 典型调用链

| POST /profiles     ↓ POST /profiles/{id}/preferences     ↓ POST /recommendations     ↓ GET /recommendations/{id}/items     ↓ POST /schemes     ↓ PATCH /schemes/{id}/order     ↓ POST /schemes/{id}/health-check     ↓ GET /schemes/{id} |
|---|

### 8.4 错误响应统一格式

| {   "error": {     "code": "SCHEME_VERSION_CONFLICT",     "message": "志愿方案已经在其他位置被修改",     "details": {}   } } |
|---|

## 9. 前端结构与交互要求

### 9.1 技术栈

- Next.js（App Router）

- TypeScript

- Tailwind CSS

- shadcn/ui

- TanStack Query（API 状态）

- Zod（前端输入校验）

- dnd-kit（96志愿拖拽排序）

### 9.2 页面路由建议

| / /onboarding/score /onboarding/preferences /recommendations/[sessionId] /schemes/[schemeId] /schemes/[schemeId]/health-check |
|---|

### 9.3 推荐卡片最小信息

| 字段 | 展示 |
|---|---|
| 学校 + 专业 | 主标题 |
| 风险层 | 高冲/冲/边界/稳/保 |
| ACS | 数值 + 解释按钮 |
| Confidence | 高/中/低 |
| Preference | 匹配度 |
| 近3年最低位次 | 趋势简表 |
| 计划人数 | 今年及历史变化 |
| 特殊要求 | 有风险时突出显示 |

### 9.4 96志愿编辑器

- 左侧候选池，右侧当前方案。

- 支持拖拽排序、添加、删除、锁定、批量移动。

- 支持按学校/城市/专业/风险层筛选。

- 重新优化必须保留 locked items。

- 每次修改产生新 SchemeVersion，而非覆盖旧版本。

- 版本冲突使用 HTTP 409。

## 10. 数据导入与版本管理

| Official Source     ↓ RAW Archive     ↓ Parser     ↓ Normalizer     ↓ University / Major / Program Series Matcher     ↓ Validator     ↓ Dataset Snapshot: DRAFT     ↓ Validation Report     ↓ PUBLISHED |
|---|

### 10.1 数据类型

- 当年招生计划

- 历年普通类常规批投档/录取结果

- 一分一段表

- 学校/专业基础信息

- 选科规则

- 高校招生章程中的特殊要求

### 10.2 发布原则

- 原始文件保留 checksum。

- 任何自动解析必须保留 source_document_id。

- 无法可靠解析的特殊规则不得自动猜测，应进入人工审核/UNKNOWN。

- 新数据发布后，已有方案通过 StaleSnapshotCheck 提醒重新体检。

## 11. 工程目录与模块边界

| repo/ ├─ frontend/ │  └─ src/ │     ├─ app/ │     ├─ components/ │     ├─ features/ │     ├─ lib/api/ │     └─ types/ │ ├─ backend/ │  ├─ app/ │  │  ├─ api/v1/ │  │  ├─ reference/ │  │  ├─ admission/ │  │  ├─ candidate/ │  │  ├─ rules/ │  │  ├─ scoring/ │  │  ├─ recommendation/ │  │  ├─ optimizer/ │  │  ├─ healthcheck/ │  │  ├─ ingestion/ │  │  ├─ db/ │  │  └─ common/ │  └─ tests/ │ ├─ docs/ ├─ docker-compose.yml └─ README.md |
|---|

### 11.1 后端边界

| Router → Service → Repository → Database  Rule Engine / Scoring / Optimizer 应保持可独立单元测试，禁止与 HTTP Request 或 ORM Session 强耦合。 |
|---|

### 11.2 不建议 V1 微服务化

V1 使用模块化单体。不要拆“推荐服务、用户服务、规则服务、数据服务”等独立部署单元。优先保证正确性、测试覆盖和可迭代性。

## 12. 开发阶段与验收标准

| 阶段 | 开发内容 | 验收 |
|---|---|---|
| 阶段0：仓库与工程基线 | 初始化 monorepo、Docker Compose、PostgreSQL、FastAPI、Next.js、lint/test。 | docker compose up 可启动前后端和数据库；README 可复现。 |
| 阶段1：数据库 | 实现全部 Alembic migration、SQLAlchemy 2 Models、seed 基础字典。 | upgrade head 成功；downgrade/upgrade 基础测试通过；FK/Unique/Check 生效。 |
| 阶段2：Profile / Preference / Catalog API | 实现考生、偏好、学校/专业/招生计划查询。 | OpenAPI 可操作；DTO 校验和版本化正确。 |
| 阶段3：Rule Engine | 选科、单科、语言、性别、体检等三态规则。 | 单元测试覆盖 PASS/FAIL/UNKNOWN 与组合规则。 |
| 阶段4：Scoring | 实现 ACS、Confidence、Preference。 | 公式测试、极端边界测试、可解释字段完整。 |
| 阶段5：Recommendation Pipeline | 构建候选池，持久化 recommendation.items。 | 相同快照+模型+输入可重放；过滤统计正确。 |
| 阶段6：Optimizer / Scheme | CP-SAT 组合优化，版本化编辑，排序。 | 能生成≤96方案；hard exclude 不泄漏；locked item 不被移动。 |
| 阶段7：Health Check | 实现资格、重复、风险分布、排序、低置信度、旧快照等检查。 | 输出 error/warning/info；不会自动修改方案。 |
| 阶段8：前端完整串联 | 六页面流程、拖拽、详情、解释、体检。 | 用户可从首页完整走到方案导出。 |
| 阶段9：ETL / Demo Dataset | 构建导入框架与演示数据。 | 数据快照可发布；来源可追溯；系统可演示。 |

## 13. 智能体总提示词（整项目版）

下面内容可以直接复制给具备代码执行和仓库操作能力的智能体。推荐先把本说明书放入仓库 docs/，再发送这段总提示词。

| 你是一名资深全栈架构师、Python/FastAPI 后端工程师、Next.js 前端工程师、PostgreSQL 数据工程师和算法工程师。现在请你在当前代码仓库中完成“山东高考智能志愿系统 V1”。  【项目目标】 这是一个面向山东夏季高考普通类考生的志愿决策系统。V1 只支持：山东省、夏季高考、普通类、常规批、第1次志愿、本科计划。系统应根据考生位次、选科、特殊报考条件、专业/学校/地域/费用偏好，以及历年招生和录取数据，生成可解释、可编辑、可体检的最多96个“专业（专业类）+学校”志愿方案。  【必须遵守的核心架构】 1. Eligibility、ACS、Preference 三层严格分离。 2. Eligibility 只负责“能不能报”，输出 PASS / FAIL / UNKNOWN。 3. ACS 只负责“历史数据下录取安全程度”，范围 0~100；禁止把 ACS 宣传为未经校准的录取概率。 4. Preference 只负责“用户有多想去”，范围 0~100。 5. FAIL 不得进入正式推荐；UNKNOWN 可进候选池但不能算保底。 6. 用户 hard_exclude 永远不得被优化器突破。 7. 组合优化和最终排序是两步。最终 1~96 顺序优先反映用户真实意愿；相似意愿内更难的志愿在前。 8. 任何政策数字（包括志愿数量上限）从数据库 admission_cycles 读取，不写死在前端或业务代码。 9. 所有推荐必须锁定 profile_version、preference_version、dataset_snapshot_id、policy_version、model_version，做到历史可重放。 10. 大模型不得覆盖 Rule Engine 的确定性结论。  【技术栈】 Frontend: Next.js + TypeScript + TailwindCSS + shadcn/ui + TanStack Query + dnd-kit。 Backend: Python 3.12+ + FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic。 Database: PostgreSQL 16+。 Cache: Redis，仅缓存公共查询类数据。 Optimization: Google OR-Tools CP-SAT。 Data processing: Polars 优先，Pandas 可作为兼容工具。 Deployment for dev: Docker Compose。  【推荐流水线】 锁定数据快照 → 标准化考生信息 → Eligibility 规则检查 → 用户硬排除 → Program Series 历史匹配 → 历史位次百分位标准化 → Base ACS → Confidence → Final ACS → Preference Score → 分层 Candidate Pool → CP-SAT 组合优化 → 最终排序 → Health Check → 保存版本/导出。  【ACS 核心公式】 q_y = historical_min_rank / historical_ranked_candidates q_c = candidate_rank / current_ranked_candidates d_y = clip(ln(q_y / q_c), -0.30, 0.30) w_y = exp(-lambda * age_y), lambda = ln(2)/1.5 M = weighted_mean(d_y) HistoryACS = 50 + 35*tanh(M/0.07) P = ln((plan_current+1)/(median(plan_history)+1)) PlanAdj = 7*tanh(P/0.40) TrendAdj = 6*tanh(T/0.04)，T 为 ln(q_y) 对年份的加权回归斜率 BaseACS = clamp(HistoryACS + PlanAdj + TrendAdj,0,100) Confidence = 0.25*历史覆盖 + 0.15*新鲜度 + 0.20*series匹配 + 0.15*来源质量 + 0.15*波动稳定 + 0.10*招生人数质量 ACS = 50 + (BaseACS-50)*Confidence 风险桶：0~29 HIGH_REACH；30~44 REACH；45~59 BORDER；60~79 STABLE；80~100 SAFE。  【Preference 默认权重】 专业35%，学校15%，地域15%，升学/学校特征10%，职业10%，学费10%，个性化5%。缺失维度从分母剔除，不能按0分处理。  【组合优化】 使用 CP-SAT。目标最大化 PreferenceUtility + ConfidenceBonus - RiskBucketDeviation - UniversityConcentrationPenalty - MajorConcentrationPenalty。不得为凑满96个加入用户拒绝的志愿。候选不足时返回实际数量并明确提示可放宽哪些条件。  【默认风险目标】 稳健：5高冲 / 10冲 / 15边界 / 36稳 / 30保。 均衡：8高冲 / 17冲 / 20边界 / 31稳 / 20保。 激进：15高冲 / 25冲 / 20边界 / 24稳 / 12保。 风险桶目标是软约束；最低安全数量可配置为硬约束。  【最终排序】 manual fixed position → desire_band DESC → ACS ASC → Preference DESC → Confidence DESC；desire_band=floor(Preference/3)。  【数据库】 必须按 reference / admission / candidate / recommendation 四个 schema 实现。核心表：universities、campuses、majors、source_documents、dataset_snapshots、admission_cycles、exam_year_stats、score_rank_segments、program_series、subject_rule_sets/groups/items、admission_plans、admission_outcomes、admission_requirements、profiles、subjects、subject_scores、attributes、preference_profiles、major_preferences、location_preferences、university_preferences、model_versions、sessions、recommendation.items、schemes、scheme_versions、scheme_items、health_checks、health_check_issues。  【API】 实现 /api/v1/profiles、preferences、catalog、recommendations、schemes、health-check、meta。必须有 Pydantic DTO、统一错误格式、OpenAPI 文档。Scheme 编辑采用 optimistic locking，版本冲突返回 409。  【前端】 必须实现：首页 → 成绩与选科 → 偏好画像 → 推荐结果 → 96志愿编辑器 → 志愿体检。推荐卡片显示学校、专业、ACS、Confidence、Preference、近年最低位次、计划变化和特殊要求。编辑器支持拖拽、增删、锁定、重新优化和历史版本。  【Health Check】 至少实现：EligibilityCheck、DuplicateCheck、ApplicationLimitCheck、UnknownRequirementCheck、RiskDistributionCheck、OrderingCheck、LowConfidenceCheck、SmallPlanCountCheck、PlanReductionCheck、StaleSnapshotCheck、HardPreferenceCheck。Health Check 只报告问题，不得擅自修改方案。  【工程要求】 1. 使用模块化单体，不要微服务化。 2. Router → Service → Repository → DB。 3. Rule Engine、Scoring、Optimizer 必须可脱离 HTTP 独立单测。 4. migration 使用 Alembic，按阶段拆分，禁止一个 migration 创建全部内容。 5. 所有关键逻辑必须有测试，尤其 ACS、资格规则、排序和优化器。 6. README 必须包含本地启动、migration、seed、测试、开发流程。 7. 不要提交密钥；提供 .env.example。 8. 不要编造真实招生数据。没有真实数据时生成 clearly-marked demo fixtures，并确保后续可替换为官方 ETL。  【工作方式】 - 先扫描仓库现状，列出已有文件、依赖和冲突。 - 若仓库为空，初始化 monorepo。 - 先实现数据库与后端核心，再做前端串联。 - 每完成一个阶段，运行测试和静态检查，并修复失败。 - 不要只输出伪代码或 TODO；直接创建可运行代码。 - 任何与本规格冲突的实现都应以本规格为准；确有技术原因需变更时，在 docs/DECISIONS.md 写明原因、替代方案和影响。 - 不要擅自扩大到艺术类、体育类、提前批、春考等 V1 外范围。  【最终验收】 最终必须做到：docker compose 可启动；Alembic 可 upgrade head；OpenAPI 可访问；前端完整走通六页面；演示数据可生成候选池和志愿方案；Rule/ACS/Confidence/Preference/Optimizer/HealthCheck 均有测试；方案版本可回溯；README 与 docs 完整。  现在请先检查仓库，给出“现状审计 + 分阶段实施计划”，随后直接开始第一个可执行阶段。不要只给建议，要实际修改代码并持续验证。 |
|---|

## 14. 分阶段 Agent Prompt

### 14.1 阶段0：工程初始化与审计

| 阅读 docs 中的项目规格并扫描当前仓库。不要假设已有技术栈。输出当前文件树、已有依赖、可复用内容和风险。随后初始化或修正 monorepo，使 frontend/ 与 backend/ 均可独立运行，并添加 docker-compose.yml、PostgreSQL、Redis、.env.example、统一 README。暂时不要实现业务算法。完成后运行前后端最小启动、lint 和基础测试，并在回复中列出实际创建/修改的文件与验证结果。 |
|---|

### 14.2 阶段1：PostgreSQL / Alembic / ORM

| 按照项目规格实现 PostgreSQL 四个 schema、枚举、核心表、FK、Unique、Check、索引以及 Alembic migrations。使用 SQLAlchemy 2 typed declarative models。migration 必须拆分为合理阶段，不能一个文件创建全部内容。增加数据库 integration tests，验证 upgrade head、关键约束和基础关系。不要实现推荐算法。完成后给出 migration 列表和测试结果。 |
|---|

### 14.3 阶段2：Profile / Preference / Catalog API

| 实现 FastAPI v1 的 Profiles、Attributes、Preferences、Catalog、Meta API。使用 Pydantic v2 DTO，Service/Repository 分层，Router 不得直接写 ORM 查询。实现 score→rank 转换接口所需的 service 抽象；如果没有真实一分一段数据，使用标记清晰的 demo fixture。实现统一错误格式和 OpenAPI。增加 API integration tests。 |
|---|

### 14.4 阶段3：Eligibility Rule Engine

| 实现完全独立于 HTTP 的 Rule Engine。支持 SubjectRule、TotalScoreRule、SingleSubjectRule、LanguageRule、SexRule、MedicalRule、QualificationRule；输出 PASS/FAIL/UNKNOWN，并保留 failures、unknowns、warnings。选科规则用 DNF：group 内 AND，group 间 OR。规则来源不明确时必须 UNKNOWN，禁止猜测。为每种规则和组合规则编写单元测试。 |
|---|

### 14.5 阶段4：ACS / Confidence / Preference

| 严格按项目公式实现 ACSService、ConfidenceService、PreferenceService。跨年份必须使用位次百分位，禁止裸位次直接比较。实现时间衰减、计划变化、趋势回归、波动度、series_match_quality、数据源质量、小招生人数收缩。ACS 是安全指数，不是概率。Preference 缺失维度重新归一权重。为极端边界、1年/3年历史、招生人数骤降、低匹配、强波动等建立完整测试。 |
|---|

### 14.6 阶段5：Recommendation Pipeline

| 实现 RecommendationPipeline：锁定 PUBLISHED snapshot 和 model_version → profile normalization → eligibility → hard exclude → history loading → ACS/Confidence/Preference → 分层 candidate pool → recommendation.items 持久化。候选池每个风险层分别截断，用户锁定项保留。推荐会话必须保存可重放版本信息和统计信息。增加 deterministic tests，确保相同输入与版本产生一致结果。 |
|---|

### 14.7 阶段6：OR-Tools Optimizer / Scheme

| 使用 OR-Tools CP-SAT 实现志愿组合优化器和排序器。Risk bucket target 是软约束；最低安全数量可作为硬约束；hard_exclude 不得突破；UNKNOWN 不计入安全数量；候选不足时允许方案少于96。实现 schemes、scheme_versions、scheme_items API，所有编辑产生新版本。支持 locked items、重新优化和 optimistic locking，冲突返回409。增加优化器和版本化测试。 |
|---|

### 14.8 阶段7：Health Check

| 实现 HealthCheckEngine，至少包含 Eligibility、Duplicate、ApplicationLimit、UnknownRequirement、RiskDistribution、Ordering、LowConfidence、SmallPlanCount、PlanReduction、StaleSnapshot、HardPreference。分 ERROR/WARNING/INFO。Health Check 只诊断，不修改方案。健康分只是产品指标，不得当成录取概率。增加每项 check 的单元测试及综合方案测试。 |
|---|

### 14.9 阶段8：Next.js 前端

| 实现 V1 六个核心页面：首页、成绩与选科、偏好画像、推荐结果、96志愿编辑器、志愿体检。使用 TypeScript、Tailwind、shadcn/ui、TanStack Query、dnd-kit。UI 简洁、可信、信息密度适中。所有风险字段显示解释入口。96志愿编辑器支持拖拽、增删、锁定、筛选、重新优化、版本冲突提示。禁止展示未经校准的“录取概率”。与真实 FastAPI 接口联调。 |
|---|

### 14.10 阶段9：ETL / Demo Dataset / 最终验收

| 实现 ingestion 框架：RAW→Parser→Normalizer→Matcher→Validator→Snapshot。不要伪造真实官方来源；如果当前没有正式数据，提供 clearly-marked demo dataset。建立 source_documents、checksum、snapshot publish 流程。随后执行最终端到端验收：docker compose、migration、API、前端六页面、推荐、优化、体检、版本回溯、测试、README。把未完成项和已知限制写入 docs/KNOWN_LIMITATIONS.md。 |
|---|

## 15. 测试、质量门槛与禁止事项

### 15.1 必测场景

- 只有分数、没有位次时，可通过一分一段表转换。

- 物理+化学要求下，物化生/物化地 PASS，物生地 FAIL。

- 特殊条件未知时输出 UNKNOWN，不得 PASS。

- 历史数据只有1年时 Confidence 自动降低。

- 招生人数从50骤降到5时 ACS 应降低。

- 历史位次剧烈波动时 Confidence 下降而非简单把 ACS 当危险分。

- 用户 hard_exclude 专业永不进入方案。

- UNKNOWN 项不计入 SAFE/STABLE 最低安全数量。

- 候选不足96时返回实际数量，不填垃圾志愿。

- 偏好更高的志愿通常排前；同 desire_band 内 ACS 更低者先尝试。

- 方案修改产生新版本；旧版本仍可读取。

- 数据快照更新后旧方案出现 STALE_DATASET warning。

### 15.2 禁止事项

| 禁止 | 原因 |
|---|---|
| 把大模型当资格规则引擎 | 政策判断必须可追踪、确定性 |
| 展示伪精确录取概率 | V1 未做概率校准 |
| 裸位次跨年直接比较 | 考生规模变化会扭曲风险 |
| 为了凑96个突破 hard exclude | 违背用户真实意愿 |
| Health Check 自动改方案 | 诊断与决策必须分离 |
| 前端写死96 | 政策参数必须来自 admission_cycle |
| 覆盖旧 preference/scheme | 破坏可追溯性 |
| V1 上微服务 | 增加无必要复杂度 |
| 无来源地自动猜体检/单科规则 | 应标 UNKNOWN 或待审核 |
| 使用未标注来源的真实招生数字 | 数据可信度不可控 |

### 15.3 Definition of Done

- 所有新增核心逻辑有测试。

- pytest、类型检查、lint 通过。

- 前后端可通过 Docker Compose 启动。

- Alembic upgrade head 可在空数据库执行。

- OpenAPI /docs 正常。

- README 从零环境可复现。

- Demo 数据与真实数据严格区分。

- docs/DECISIONS.md 记录偏离规格的技术决策。

## 16. 最终交付清单

| 交付物 | 要求 |
|---|---|
| frontend/ | 可运行 Next.js V1 六页面 |
| backend/ | FastAPI + SQLAlchemy + Alembic + 推荐逻辑 |
| docker-compose.yml | 本地一键启动 PostgreSQL / Redis / backend / frontend |
| .env.example | 不含真实密钥 |
| migrations/ | 完整、分阶段、可升级 |
| tests/ | unit + integration |
| demo data | 明确标注为 DEMO，不能冒充官方真实数据 |
| docs/ARCHITECTURE.md | 架构与模块边界 |
| docs/ALGORITHM.md | ACS / Confidence / Preference / Optimizer |
| docs/API.md 或 OpenAPI | 接口说明 |
| docs/DECISIONS.md | 架构决策记录 |
| docs/KNOWN_LIMITATIONS.md | V1 限制 |
| README.md | 安装、启动、migration、测试、演示 |

### 16.1 推荐给智能体的实际执行方式

1. 先把本交接文档放入仓库 docs/，并在根目录 README 引用。

1. 第一次使用“总提示词”，让智能体完成仓库审计并开始阶段0。

1. 之后每完成一个阶段，使用对应“分阶段 Agent Prompt”继续，不建议一次要求智能体同时完成所有阶段。

1. 每阶段要求智能体真实运行测试，而不是只声称“应该可运行”。

1. 如果智能体修改了核心模型或公式，要求同步更新 docs/DECISIONS.md 和 ALGORITHM.md。

1. 在接入正式招生数据前，只允许使用 demo fixtures 做功能开发和回归测试。

| 建议 真正交给智能体长期开发时，最重要的不是提示词写得越长越好，而是把本规格作为仓库内长期上下文，并要求 Agent 每个阶段读取 docs、运行测试、记录决策。总提示词负责“立规矩”，阶段提示词负责“控制范围”。 |
|---|

## 附录 A：术语速查

| 术语 | 定义 |
|---|---|
| Eligibility | 报考资格结果：PASS / FAIL / UNKNOWN |
| ACS | Admission Confidence/Safety Score，录取安全指数0~100，非录取概率 |
| Confidence | ACS 的数据可信度0~100 |
| Preference | 用户主观匹配度0~100 |
| Program Series | 用于跨年份对齐同一学校同一招生专业/专业类的稳定序列 |
| Candidate Pool | 经资格、硬条件和风险分层后进入优化器的候选集合 |
| Scheme | 一套志愿方案 |
| SchemeVersion | 该方案某次编辑/优化后的不可变版本 |
| Dataset Snapshot | 一次可用于推荐的已发布招生数据版本 |
| Health Check | 对最终方案做规则、风险、排序与数据时效诊断 |
