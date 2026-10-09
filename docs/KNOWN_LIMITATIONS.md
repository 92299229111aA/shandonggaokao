# V1 已知限制

本文档随开发推进持续更新。**未完成项与真实约束必须记录在此，不得隐瞒。**

## 环境与验证限制

1. **`docker compose up` 未在本机实测。**
   本机 Docker Desktop 因内存不足无法启动 Linux VM
   （`系统内存不足，无法启动虚拟机 DockerDesktopVM，需要内存 2048 MB`，宿主机 7.4 GB）。
   `docker-compose.yml` 已按规格完整编写，但需要在**可用内存 ≥ 2 GB（建议 ≥ 4 GB）**的机器上
   才能完成端到端验证。详见 `docs/DECISIONS.md` D-001。
   本机的数据库层验收改在 WSL2 原生 PostgreSQL 16.15 上真实执行。

2. **`frontend/pnpm-lock.yaml` 由 pnpm 11 生成。**
   若使用 pnpm 9/10，`pnpm-workspace.yaml` 中的 `allowBuilds` 字段不被识别，
   需要改回 `onlyBuiltDependencies`（或在旧版本下用 `pnpm approve-builds`）。

## 数据限制

3. **已接入真实数据，但来源是第三方整理包，不是省考试院官方发布。**

   当前快照 `SD-2022-2025`：山东 2022–2025 普通类常规批第 1 次志愿本科计划，
   1,246 所院校（覆盖 32 个省级行政区）、73,198 条招生计划、72,885 条历史录取、
   1,033 行一分一段、64 种选科规则集。

   溯源标记：`source_documents.quality = 'THIRD_PARTY'`、`is_demo = false`。
   `/api/v1/meta/dataset` 会返回明确警告（「来自第三方整理包，请以官方《填报志愿指南》核对」）。
   第三方来源会通过 Confidence 的数据源质量项（权重 0.15）让 ACS 略保守。

   **尚未覆盖**（逐项见 `docs/DATA_IMPORT.md`）：2026 年招生计划（整理包中未包含）、
   专科（普通类二段）、提前批/地方专项/高校专项。

   **专业代码跨年会变**：实测 2023→2024 命中 83.5%、2024→2025 命中 84.3%，
   约 16% 的专业因代码复用或名称变更无法跨年对齐。未对齐的历史不会参与 ACS，
   由 Confidence 如实反映（`history_points` 会偏低）。

4. **高校招生章程的特殊要求无法自动全量解析。**
   身高、视力、色觉、单科成绩、语种、性别等要求散落在各校章程文本中。
   无法确定性解析的条目一律 `admission_requirements.is_deterministic = false`，
   Rule Engine 对其输出 `UNKNOWN`，**绝不猜测为 PASS**。
   真实数据里专业名称括号内已有「色盲考生不予录取」这类文本，但**尚未解析成规则**，
   目前该志愿按 UNKNOWN 处理。

## 算法限制

5. **ACS 不是录取概率。** V1 未做概率校准（无历史预测-实际对照回测）。
   任何界面、接口、文档都不得把 ACS 表述为"录取概率"。

6. **ACS 依赖 Program Series 的跨年份匹配质量。**
   专业更名、专业类合并/拆分只能靠 `series_match_quality` 降级表达，
   匹配质量差会通过 Confidence 收缩 ACS 向 50 靠拢，但**无法还原真实对应关系**。

7. **趋势项使用 `ln(q_y)` 的加权线性回归。**
   历史点少于 2 个时无法拟合，此时 `TrendAdj = 0` 并由 Confidence 体现不确定性。

8. **一分一段表缺失时无法由分数换算位次。**
   `POST /api/v1/catalog/score-to-rank` 在无对应年份数据时返回明确的
   `RULE_VIOLATION` 错误，提示考生直接填写位次；不做任何插值猜测
   （相邻分数回退会在响应的 `source` / `note` 中标注为 `INTERPOLATED`）。

## 范围限制（设计如此，非缺陷）

9. V1 仅支持：山东、夏季高考、普通类、常规批、第 1 次志愿、本科计划。
   提前批、艺术类、体育类、春季高考、常规批第 2/3 次志愿、专科完整推荐均**不支持**，
   且在数据库 CHECK 约束层被拒绝。

10. V1 **不代表考生在官方系统提交志愿**，只输出可导出的决策辅助方案。

11. Health Check 的 `score` 只是产品指标，不是录取概率；Health Check 只诊断，
    **不会自动修改方案**。

## 待办（阶段推进中更新）

| 阶段 | 状态 |
|---|---|
| 0-9 | 全部完成（见 `docs/PHASE_PLAN.md`） |

## 尚未实现 / 刻意未做

- **ACS 的"保"层（SAFE，ACS≥80）在当前参数下基本不可达。**
  `d_y` 截断在 ±0.30，叠加 `ACS = 50 + (BaseACS-50) × Confidence` 的收缩效应后，
  ACS 的实际上限约 **77.6**（confidence ≈ 0.79 时）。这与规格里"稳健模式 20 个
  ACS≥80 / 均衡 12 个"的默认目标直接冲突，也与 SAFE 桶定义为 ≥80 冲突。
  推导过程与三种可选处理方式见 [`docs/DATA_IMPORT.md`](DATA_IMPORT.md) 第六节；
  验证脚本 `backend/ops/check_acs_ceiling.py`。
  **未擅自修改规格，等你决策后再同步更新 `docs/ALGORITHM.md` 与 `model_versions`。**
- **没有独立的前端 E2E 测试（Playwright 等）**：验收通过 `ops/e2e_check.py` 直接打真实
  HTTP 接口覆盖完整业务链路（52 项断言），并用 `next build` 验证页面可编译。
  浏览器内的交互（拖拽、弹窗）尚未有自动化测试。
- **没有导出原生 Excel（.xlsx）**：前端导出 CSV（带 BOM，Excel 可直接打开）。
  后端已装 `openpyxl`，需要时可扩展。
- **真实数据导入尚未覆盖的部分**（逐项列在 `docs/DATA_IMPORT.md` 末尾）：
  - 院校基础数据、专业目录**没有上传接口**，需直接写 `reference.universities` /
    `reference.majors`（`ministry_code` 不能以 `DEMO` 开头，否则会被重置逻辑清掉）。
  - 选科要求与招生章程特殊要求目前**只存原始文本，尚未解析成 DNF 分组**。
    未解析时该计划按 UNKNOWN 处理（不会误判为可报），但会让大量志愿无法计入保底。
  - `app/ingestion/etl.py` 定义的是通用 Parser/Normalizer/Validator 契约，
    具体解析器按官方文件格式实现（当前三个 CSV 导入器是它的第一组实现）。

## 已实现部分的行为边界（截至阶段 5）

- **`exam_year_stats` 缺失时流水线不产出 ACS**：由于跨年比较必须做位次百分位标准化，
  缺少当年考生人数时流水线会明确写入 `stats.notes` 并返回 0 个候选，而不是用裸位次硬算。
  生产数据必须包含每一年的 `ranked_candidates`。
- **FAIL 记录只保留有限样本**：`recommendation_items` 会保存进入候选池的全部条目，
  以及最多 200 条 FAIL 记录用于审计（`PipelineConfig.persist_fail_sample`），
  避免 2 万+ 计划全部落库。需要完整审计时调大该值或另行落审计表。
- **特殊要求为自由文本时一律降级为 UNKNOWN**：`admission_plans.special_requirements`
  有内容时该志愿不会是 PASS，因此不会被计入保底数量。
- **专业名称硬性排除采用"整名或分隔符边界"匹配**：排除「专业2」不会同时排除「专业20」，
  但会排除「专业2(中外合作)」。若用户的排除词与官方专业名差异较大，需要改用
  `major_id` / `discipline_code` 精确排除。
- **`min_safe_count` 在候选不足时被钳制**：若满足 ACS≥60 且资格为 PASS 的候选少于要求数量，
  优化器会把硬约束放宽到实际可用数量（否则模型无解），并在 `relax_suggestions` 中说明。
- **UNKNOWN 资格不参与保底计数**：`counts_as_safe = (eligibility == 'PASS')`，
  因此大量 UNKNOWN 志愿会拉低方案的可保底数量，这是刻意的保守设计。
- **锁定志愿作为硬约束进入 CP-SAT 模型**：`required_item_ids` 在模型内被固定为 1，
  所以志愿总数上限始终成立；锁定数超过目标数量时返回明确的 `RULE_VIOLATION` 而不是静默超出。
- **健康分按「问题类别」扣分，不按条数扣分**：100 分起，每类问题扣一次
  （ERROR 15 / WARNING 5 / INFO 1），同类多出的条目每条加 1 分、单类封顶 3 分，下限 0。
  明细仍逐条列出，只是计分聚合。这是真实数据校准的结果 —— 详见 `DECISIONS.md` D-022。
- **`PLAN_EXPANDED`（招生计划扩张）不参与扣分**：计划扩张对考生有利（名额增加会拉低
  录取位次），仍会展示供参考，但不应降低健康分。缩减（`PLAN_REDUCED`）仍然扣分，
  这个不对称是刻意的。
- **健康分是固定扣分模型**：任何分数都能手算复核，不代表任何真实录取可能性。
- **`OrderingCheck` 只报告第一处顺序问题**：为避免一份体检报告刷出上百条同类提示，
  每类顺序问题（意愿倒挂 / 同层 ACS 倒挂）只报第一条，修复后再次体检可发现下一条。
- **`EligibilityCheck` 默认复用推荐时的资格结论**：只有传入
  `recheck_eligibility=true` 才会按当前选科重新核对，并在报告中给出 INFO 说明。
  这是因为重新核对需要重新加载全部计划与规则，成本较高，作为显式选项更诚实。
