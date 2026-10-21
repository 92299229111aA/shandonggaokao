# 专业工作台六页 · 可行性审查

> 审查对象：`Gaokao_Workbench_Requirements_and_Codex_Prompts (1).md`（V2.0，860 行）+ `index.html`（原型，191 行 / 82KB）
> 审查基准：本仓库现有代码（Next.js 7 页 + FastAPI 36 个接口）
> 结论：**可行，但有不小的工作量；有 8 处需要你先拍板**
> 本文件只做审查，未改动任何代码。

---

## 一、结论先说

**技术上完全可行，而且后端能力比文档假设的更全。** 36 个接口里，六页需要的核心能力（档案、偏好、推荐、方案、版本、体检、导出）**都已有真实实现**，不需要造后端。

但这不等于"改改界面"：

| | 工作量 |
|---|---|
| 页面从 7 个变 6 个，且**全部重写**（含新的工作台外壳：顶栏 + 侧边栏 + 主区） | 大 |
| 设计语言整体替换（文档的 `#2458B8` 体系 vs 现有 shadcn 蓝） | 中 |
| 新增 P00 主页（当前没有总览页） | 中 |
| 过期状态（推荐过期 / 报告过期）贯通六页 | 中（可客户端推导，不改库） |
| 体检三态（通过/未通过/待核验）+ 问题定位回跳 | 中（需一层映射） |
| 响应式 375–1920 | 中 |
| 导出 CSV/XLSX 文件下载 | 小 |
| 现有 52 项 E2E 与 22 项后端测试**全部重做验收** | 中 |

**结论：这是一次界面层重写，不是改版。** 后端基本不动，前端基本重做。

---

## 二、两份材料是什么

**文档（V2.0）**：六页完整需求 + 内嵌提示词。它是**验收标准**，不是设计稿。里面的强项是"不许造假"的规则极多（未知不等于通过、缺失不等于 0、满额不等于安全、过期不等于有效）。

**原型（index.html）**：**可运行的单文件 SPA**，不是静态图。

| 事实 | 数值 |
|---|---|
| 页面 | 6 个视图（home / profile / preferences / recommendations / editor / checkup）|
| 架构 | 两个 script 块：`GK`（状态 + 逻辑，8.9KB）+ UI 渲染（47KB）|
| 演示数据 | **13 条**院校专业样例，内存状态，**无 localStorage** |
| 外部依赖 | 0（样式、图标全部内联）|
| 375px 横向溢出 | 无（已实测）|
| 控制台错误 | 无 |

原型自己标了 5 处"示例数据/未接入"，诚实程度符合文档要求。

---

## 三、六页 × 现状对照

| 页 | 文档要求 | 后端现状 | 前端现状 | 结论 |
|---|---|---|---|---|
| **P00 主页** | 当前任务、五步进度、档案/偏好摘要、当前方案、待办、最近体检 | 无专用接口，但可从 `GET /profiles`、`GET /recommendations/{id}`、`GET /schemes/{id}`、`GET /health-checks/{id}` 拼出 | **不存在** | **新建** |
| **P01 成绩与选科** | 年份/类别/位次/分数/选科/资格补充，草稿与保存分离 | **齐全**：`ProfileDTO` 有 `status`(含 DRAFT)、`version`、`rank_source`、`subjects`；`POST /catalog/score-to-rank` 可用；`PATCH /profiles/{id}` | 现有 `/onboarding/score` 已有基础校验 | **重写** |
| **P02 偏好画像** | 地区/专业/院校/费用/项目接受度/策略，硬限制与软偏好分离 | **超预期**：`LocationPreferenceIn`/`MajorPreferenceIn`/`UniversityPreferenceIn`/`OtherPreferenceIn` 全部支持 `polarity`(INCLUDE/EXCLUDE)、`is_hard_exclude`、`weight`；`max_tuition`/`exclude_private`/`exclude_cooperative`/`dimension_weights` | 现有 `/onboarding/preferences` 只用了部分字段 | **重写（能力已就绪）** |
| **P03 推荐结果** | 全池搜索、风险/地区/标签筛选、排序分页、详情、加入 | **齐全**：items 带 `risk_bucket`/`acs`/`confidence`/`eligibility`/`plan_count`/`tuition_fee`；`bucket_counts`；`university-groups` | 现有 `/recommendations` 已是列表+分页+筛选 | **重写布局** |
| **P04 志愿编辑器** | 真实序号、锁定、上下移、过滤禁排序、优化预览、版本 | **齐全**：`lock_version` 乐观锁、`PATCH /order`、`PUT /lock`、`POST /optimize`、`GET /versions` | 现有 `/schemes` 逻辑完整（含过滤禁拖拽） | **重写布局** |
| **P05 志愿体检** | 三态检查、严重程度、问题定位、导出 | **基本齐全**：`HealthCheckDTO` 有 `score`/`error_count`/`warning_count`/`info_count`/`passed`/`checks[]`/`issues[]`；issue 含 `code`/`severity`/`message`/`position`/`detail`/`suggestion` | 现有 `/health-check` 已展示报告 | **重写 + 加映射层** |

**后端不用新建表、不用改算法。** 这是我审查后最放心的一点。

---

## 四、后端已有、但文档假设"可能没有"的能力

这几个直接省掉大量工作：

1. **偏好硬限制是真实存在的**，不是 UI 概念。`is_hard_exclude=true` 的项在优化器里不可突破（这条是我们已有的铁律），所以文档 §6.3 要求的"硬限制/软偏好分离"可以直接落到真实字段上。
2. **推荐会话已经记录了版本钉子**：`SessionDTO` 带 `profile_version`、`preference_version`、`snapshot_id`、`model_version`、`policy_version`。文档 §3.3 要求的"推荐绑定输入版本"**已经实现**。
3. **体检报告已经绑定了方案版本**：`HealthCheckDTO` 有 `scheme_version_id` 和 `version_no`。文档 §9.5 的"报告对应明确版本"**已经实现**。
4. **方案有真版本号和乐观锁**：`current_version_no` + `lock_version`，冲突返回 409。文档 §8.6 的"版本冲突不静默覆盖"**已经实现**。
5. **导出接口已存在**：`GET /schemes/{id}/export` 返回 `ExportResponse`（含 `scheme_id`/`version_no`/`target_count`/`application_limit`/`disclaimer`/`rows`）。只缺"下载成文件"这一步。

---

## 五、五个真实缺口（必须做，但都不是硬骨头）

### G1 过期状态没有后端字段

文档 §3.3 要求：改档案/偏好 → 旧推荐过期；改方案 → 旧报告过期。**后端没有 `is_stale` 字段。**

**可行方案（不改库）**：客户端比对版本号推导。

```
推荐过期 ⟸ session.profile_version ≠ profile.version
          或 session.preference_version ≠ 最新 preference.version

报告过期 ⟸ check.scheme_version_id ≠ scheme 当前版本的 id
```

数据都在手上，**能准确判断**。代价是六页都要接同一套推导，且"方案需重新资格检查"这类没有直接字段的状态要额外约定。

### G2 体检只有两态，文档要三态

`HealthCheckCheckDTO` 是 `passed: boolean` + 三个计数。文档要求：**通过 / 未通过 / 待核验**（外加"不适用"）。

**可行方案**：用 issue 的 `code` 做映射层。我们实际产出 **25 个 code**，可归为：

| 目标状态 | code |
|---|---|
| **未通过（阻塞）** | `ELIGIBILITY_FAIL`、`DUPLICATE_PLAN`、`LIMIT_EXCEEDED`、`HARD_PREFERENCE_VIOLATED`、`PLAN_COUNT_ZERO`、`SNAPSHOT_MISSING` |
| **待核验** | `ELIGIBILITY_UNKNOWN`、`UNKNOWN_REQUIREMENT`、`UNKNOWN_IN_SAFETY_NET`、`SNAPSHOT_TOO_OLD`、`STALE_DATASET`、`SNAPSHOT_NOT_PUBLISHED` |
| **警告** | `MIN_SAFE_COUNT_UNMET`、`RISK_TARGET_SHORTFALL`、`NO_SAFETY_NET`、`THIN_SAFETY_NET`、`ORDER_ACS_INVERSION`、`ORDER_DESIRE_INVERSION`、`SMALL_PLAN_COUNT`、`LOW_CONFIDENCE` |
| **说明** | `PLAN_EXPANDED`、`PLAN_REDUCED`、`LIMIT_UNUSED`、`ELIGIBILITY_RECHECKED`、`DEMO_DATA` |

这张表要落到一处集中配置（文档 §10.2 也要求"演示阈值集中在一处"）。

### G3 导出只有 JSON，没有文件

文档说"沿用已有格式"。现状是 JSON。需要：CSV（UTF-8 + 公式注入防护，文档 §11.4 明确要求）和/或 XLSX。

### G4 P00 主页是全新页面

当前没有总览页。数据可从 4 个现有接口拼，但要处理"哪一次推荐/哪一个方案是当前的"这层选择逻辑。

### G5 现有路由与新路由不一致

| 现有 | 文档建议 |
|---|---|
| `/onboarding/score` | `/profile` |
| `/onboarding/preferences` | `/preferences` |
| `/schemes` | `/editor` |
| `/health-check` | `/checkup` |
| `/schemes/versions` | （原型里是"历史版本"弹窗）|

文档 §1.2 明确说"现有项目已有路由时沿用，不为统一名称破坏书签"。所以我倾向**保留现有路由**，需要时加别名。

---

## 六、需要你拍板的 8 个问题

| # | 问题 | 我的建议 |
|---|---|---|
| **Q1** | 视觉以谁为准？你的新文档+原型（`#2458B8`、左侧栏工作台）还是我之前做的 P0 稿（`#1D4E89`、顶部步骤条）？ | **以新文档+原型为准**，P0 稿作废存档 |
| **Q2** | 路由用现有（`/onboarding/score`…）还是改成 `/profile` `/editor`…？ | 保留现有，加别名重定向 |
| **Q3** | 过期状态用"客户端按版本号推导"（不改后端）可以吗？ | 可以，够准确 |
| **Q4** | 体检三态用 code 映射表实现可以吗？ | 可以，映射表集中一处 |
| **Q5** | 六页是**重写**（丢弃现有 7 页），还是保留旧页面并存？ | 重写，旧的删掉 |
| **Q6** | 导出要 CSV、XLSX，还是两个都要？ | CSV 必做，XLSX 看你要不要 |
| **Q7** | `/schemes/versions` 独立页保留，还是按原型改成"历史版本"弹窗？ | 改成弹窗（少一个页面） |
| **Q8** | 手机端（375–768）这轮要不要做全？文档要求做，但工作量约占三分之一 | 建议**桌面做透 + 手机只保核心路径**（表单、筛选、加入、上下移、体检查看） |

---

## 七、工作量与分批

按文档 §12 的推进顺序，但合并成可验收的批次：

| 批次 | 内容 | 预估 |
|---|---|---|
| **A 外壳与设计系统** | 顶栏 + 侧边栏 + 主区、设计令牌（文档 §2.2）、共享组件（SaveStatus/FreshnessNotice/RiskBadge/FilterBar/EmptyState 等）、路由与全局状态 | 3–4 天 |
| **B P00 主页** | 六类进度状态、动态主按钮、待办、最近体检 | 2 天 |
| **C P01 + P02** | 档案与偏好两页，真实保存 + 校验 + 下游过期 | 3–4 天 |
| **D P03 推荐结果** | 全池搜索、筛选、分页、详情面板、加入 | 3 天 |
| **E P04 编辑器** | 真实序号、锁定、上下移、过滤禁排序、优化预览 | 3–4 天 |
| **F P05 体检** | 三态、问题定位回跳、导出 | 2–3 天 |
| **G 联调与响应式** | 过期链路贯通、375/768/1280/1440/1920、键盘、验收用例 E01–E17 | 3–4 天 |

**合计约 19–24 天**（不含桌面封装）。这比之前 `DESKTOP_APP_PLAN.md` 里估的 P3+P4 大了不少 —— 因为那是"换皮 + 重排"，这是"六页重写 + 新外壳"。

---

## 八、与桌面化计划的关系

`DESKTOP_APP_PLAN.md` 里的 **P0（设计稿）已被本文件取代**，**P3/P4（设计系统 + 页面重排）被本文档的六页取代**。

剩下不冲突、仍要做的部分：

- **SQLite 适配 + Electron 封装 + 安装包**（原 P1/P2/P5/P6 里的打包与更新部分）
- 顺序建议：**先把六页在浏览器里做完做对，再封装**。理由：六页重构会大改路由与组件，如果先封装，每次都要重新打包才能看效果，反馈太慢。

---

## 九、我会怎么验证（不接受"看起来对"）

- 每批次跑现有**后端 22 项测试 + ruff + mypy**（后端不动，必须一直绿）
- 起真实服务（PostgreSQL + uvicorn + next dev），用真实数据
- **Playwright 逐页截图 + 关键交互断言**：参考现成的 `walk_full_flow.mjs`
- 按文档 §13.1 的 **E01–E17 用例**逐条验，验不了的如实写"未验证"
- 六页完成矩阵（文档 §17 要求）：每页列出已实现字段 / 交互 / 接口 / 状态

**不做的**：不编造通过结果，不把"未接入"写成"已实现"，不在没有真实数据的地方显示假统计。

---

## 十、三个必须提前说清的风险

1. **现有 52 项 E2E 会大面积失效** —— 页面重构必然改 DOM 与流程。我会重建验收脚本，但要接受"旧脚本作废"。
2. **文档里有一处我没法单方面决定的数据矛盾**（文档 §10.2 自己也标了）：原稿 ACS 80.0 标"稳"，而色板写 80–100 是"保"。我核对过**我们后端与原型是一致的**（79.9 → 稳，81.8 → 保，阈值 80），所以按后端走。但如果你手上有官方口径不同的说法，需要告诉我。
3. **"真实性"是这份文档的核心要求，而原型是 13 条演示数据。** 实现时我会直连真实后端（19,019 条候选），不保留"演示模式"，否则六页的计数与筛选口径会两套逻辑。如果你想要一个可对外演示的 demo 版本，那是另一份工作量。

---

## 十一、请你确认

最少需要定这 4 个，我就能开工：

1. **Q1 视觉**：以新文档+原型为准（我理解是这样，请确认）
2. **Q5 六页重写**：旧的 7 页删掉
3. **Q8 手机端**：做全，还是桌面做透 + 手机保核心
4. **Q6 导出**：CSV 还是 CSV + XLSX

其余（Q2/Q3/Q4/Q7）按我建议执行，有异议随时说。
