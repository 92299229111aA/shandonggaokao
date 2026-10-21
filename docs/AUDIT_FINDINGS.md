# 全量测试发现与修复计划

> **状态：P1–P5 已修复并验证（阶段 15）。** 本文件保留为发现记录；
> 修复内容与验证证据见 [DECISIONS.md](DECISIONS.md) 的 **D-028**。
> 未修复项：P6（目录缺军事学，属来源局限）、P7（已知覆盖率缺口）、
> P8（WSL 环境）、P9（我的审计脚本缺陷，已修正）。

**日期**：阶段 14
**范围**：代码级校验、接口验收、数据一致性、算法不变量、边界与错误路径、浏览器级验证、迁移一致性
**当时状态**：只诊断，未修复（现已按第五节的决定全部修复）。

---

## 一、总体结论

项目主体是健康的：**345 项测试全通过、E2E 52/52、ruff/mypy/tsc/eslint/build 全部干净**，
数据无悬空引用、无重复自然键、算法不变量（权重合计、风险桶边界、体检扣分算术、
优化器硬约束、唯一性）逐项核对全部正确。

但发现 **2 个高危缺陷 + 1 个基础设施缺陷 + 3 个中危问题 + 3 个低危/环境项**。
其中 P1 是**上一轮修改引入的回归**，让「保底」这个核心承诺整体失效；
P5 让并发编辑返回 500 而不是承诺的 409。

---

## 二、问题清单

### P1（高危）`special_requirements` 被当成两件事用，导致资格 100% UNKNOWN

**证据**

```
非 DEMO 计划            73,198
有 special_requirements 73,198  (100.0%)
  其中只是「院校专业序号」 73,198
  其中是其它文本                0

最近 6 个推荐会话的资格分布：
  UNKNOWN  19,019   ← 全部
  FAIL        200
  UNKNOWN 原因码（抽样 2,000 条）：SPECIAL_REQUIREMENT_UNVERIFIED × 2000
```

**成因**

上一轮我把「院校专业序号」写进了 `admission_plans.special_requirements`
（当时为了不改表结构而选了这个字段）。但资格引擎把任何非空的
`special_requirements` 解读为**招生章程中尚未确认的特殊要求**，于是给出 UNKNOWN。

**后果（实测）**

| 影响 | 数值 |
|---|---|
| 资格为 UNKNOWN 的候选 | 19,019 / 19,019 |
| 其中 ACS≥60（本可作保底） | **13,481 条全部不计入保底** |
| 保底计数 | **恒为 0** |
| 用户看到的「稳/保」风险承诺 | **整体失效** |

按代码规则 `counts_as_safe = (eligibility == 'PASS') and acs >= 60`，
UNKNOWN 一律不计入安全数量 —— 这是**刻意的保守设计**（不猜测为可报），
但正因为如此，把无意义的文本塞进那个字段就等于把保底全部关掉。

**注意**：这也是一个**好设计暴露出的问题**。系统没有把未确认的要求猜成 PASS，
而是如实标 UNKNOWN —— 行为是对的，是我的数据写入方式错了。

---

### P2（高危，基础设施）开发库与迁移文件不一致，且没有任何机制能发现

**证据**

```
开发库 gaokao       alembic_version=0008_recommendation   匹配索引 0 个
测试库 gaokao_test  alembic_version=0008_recommendation   匹配索引 4 个
```

完整结构对比（4 个 schema、32 张表、全部列/索引/约束）：

```
[索引] !! 开发库缺失: candidate.location_preferences.ix_location_preferences_preference_profile_id
[索引] !! 开发库缺失: candidate.major_preferences.ix_major_preferences_preference_profile_id
[索引] !! 开发库缺失: candidate.other_preferences.ix_other_preferences_preference_profile_id
[索引] !! 开发库缺失: candidate.university_preferences.ix_university_preferences_preference_profile_id
[索引] 仅开发库有:     candidate.other_preferences.ix_other_preferences_profile_id
差异合计: 5 处
```

**成因**：迁移文件在**已被应用之后**被修改过（0007 里加了索引、改了索引名），
但开发库没有重跑迁移。版本号相同，表结构却是旧的 —— 于是
`alembic_version` 不再描述真实结构。

**为什么严重**：这类漂移**不会被任何现有检查发现**。
`pytest` 用测试库（新建并完整迁移，所以正确）；`alembic check` 不在任何脚本里。
开发库可以无声地偏离迁移文件任意久。

**`alembic check` 的直接输出不可用**：除上述 5 处，它还报约 70 项
`modify_default`（DDL 有 `server_default`、模型只写 Python `default=`，是 D-005 的已知噪音）
和 `remove_table alembic_version`（autogenerate 记账表，误报）。

---

### P3（高危）并发编辑方案时未捕获唯一约束冲突，返回 500 而非承诺的 409

**证据**（连续 3 次运行，结果完全一致）

```
两个 PATCH /schemes/26/order 同时提交同一版本号：
  请求 1: HTTP 200
  请求 2: HTTP 500
```

服务端日志：

```
sqlalchemy.exc.IntegrityError: (psycopg.errors.UniqueViolation)
  duplicate key value violates unique constraint "uq_scheme_versions_scheme_id_version_no"
  DETAIL:  Key (scheme_id, version_no)=(26, 2) already exists.
  [SQL: INSERT INTO recommendation.scheme_versions (... version_no ...)]
```

**成因**：两个请求**都通过了版本号检查**（各自读到 `version_no = 1`），
然后**都尝试插入 `version_no = 2`**。第二个撞上唯一约束，
异常没有被捕获成业务错误，直接冒泡为 500。

**为什么严重**：产品文档明确承诺这种情况会返回
「方案已在其他位置被修改」（409，乐观锁），且把这一点作为卖点写进了产品介绍。
实际行为是服务端错误 —— 用户看到的是「服务器故障」而不是「你的修改没保存，请刷新」。
**这会让人误以为保存成功或系统崩溃，而真实情况是版本冲突。**

**为什么没被测出来**：E2E 与单元测试都只发**单个**请求并检查版本号校验是否生效，
没有任何测试让两个写请求真正并发。

---

### P4（中危）只选 2 门选考科目可以通过校验

**证据**：`POST /profiles` 传 `subjects: ["PHY","CHM"]` 返回 **201**，
随后建档、建偏好、跑推荐全部成功，产出 18,643 条候选。

**为什么是问题**：山东实行 3+3，考生的选考科目**恰好 3 门**。
2 门档案的资格判断没有意义（3 门要求永远无法满足），却能得到一份看起来正常的完整方案。
实测该档案下 19,722 条计划的资格**全是 UNKNOWN**（原因同上 P1，但即使 P1 修好，
2 门档案依然会大面积 UNKNOWN，因为选科规则要 3 门）。

**同时确认的对照**：3 门但重复（`["PHY","PHY","CHM"]`）与非法代码（`XXX`）**都被正确拒绝（422）**，
说明校验是有的，只是没校验**数量**。

---

### P5（中危）位次超出全省达线人数时不报错也不提示

**证据**：2025 年普通类一段达线 333,469 人，但位次 600,000 的档案返回 **201**，
并产出 19,019 条候选；`stats.notes` 为**空**。

**后果**：位次百分位 `q_c = rank / population > 1`，
所有历史项都显得「比考生差」，ACS 系统性偏移；风险分布退化为
`HIGH_REACH 18,545 / REACH 447 / BORDER 27`，**没有任何稳/保**。
用户不会被告知这个位次在当前数据下不可能。

---

### P6（低危）结构化专业目录缺军事学，5 个门类在真实数据中无映射

**证据**：目录解析覆盖 12 个门类，**没有军事学（11）**；
真实数据门类分布为
`01:1, 02:49, 03:41, 04:20, 05:79, 06:13, 07:130, 08:478, 09:32, 10:65, 12:136, 13:10`
—— 军事学（11）与交叉学科（14）**一条都没有**。

**性质**：这是**来源局限**，不是代码缺陷（已用测试断言这 12 个门类）。
但当军队院校计划进入数据时，这些志愿会没有门类，按学科筛选时被漏掉。

---

### P7（低危，已知缺口）部分覆盖率偏低

| 字段 | 覆盖率 | 原因 |
|---|---|---|
| `reference.majors.discipline_code` | 1,054 / 1,119（94.2%） | 试验班、专科专业名、新专业 |
| `program_series.major_id` | 21,526 / 25,280（85.2%） | 同上 |
| `admission_outcomes.plan_count` | 67,069 / 72,885（92.0%） | 源文件部分年份缺计划数 |
| `reference.universities.city_name` | 1,119 / 1,153（97.1%） | 军队院校与新设院校不在教育部名单 |

均为**已知且刻意不猜测**，不需要"修复"，但应记录在文档里。

---

### P8（环境，非代码缺陷）WSL 空转停机导致长时间任务中断

本次审计期间 PostgreSQL 两次因 WSL 空闲关机而中断，其中一次
**恰好发生在 ETL 写入过程中**，事务未提交、需要重跑。
属本机环境限制，需要文档化的规避建议（例如固定 WSL 常驻）。

---

### P9（我的审计脚本缺陷，非产品缺陷）

`audit_data_integrity.py` 起初报「门类代码越界 843 行」，
实为**我的正则写错**：`^([0-9]|1[0-4])$` 只匹配 1 位，而门类码是 2 位。
已修正为 `^(0[1-9]|1[0-4])$`，复测 0 行越界。
记录在此以免与真实缺陷混淆。

---

## 三、修复之间的依赖与冲突

### 冲突 1（必须协调）：P1 与 P2 都要改数据库结构

- **P1** 需要给 `admission_plans` 加一个承载序号的列（或用别的字段），**需要迁移**。
- **P2** 需要在开发库上重跑迁移（`downgrade 0007 → upgrade head`）。

**冲突点**：两次 `alembic upgrade` 作用在同一个库上；若 P2 用 `downgrade/upgrade`
而 P1 又插入了新迁移，顺序错了会丢数据或报 revision 冲突。

**建议的合并做法**：把两者合成**一次**操作 ——
写一个新迁移 `0009_*` 同时完成「修正字段用途」（P1），
然后用**干净重建**（`drop schema` + `alembic upgrade head`）取代 `downgrade/upgrade` 来消除 P2 的漂移。
重建还会顺带验证「从零迁移」这条路径（那正是测试库一直在用的、正确的路径）。

**需要你确认的点**：干净重建会**清空开发库的考生档案与推荐会话**
（院校/专业/计划/录取等参考数据可由 ETL 重新载入，约 9 分钟）。
是否接受？若需要保留现有会话，则改用
`alembic downgrade 0006 → upgrade head`（只重建 candidate schema 之后的部分）。

### 依赖 2：P4、P5 的校验阈值应在 P1 修好后校准

P4（选考科目数量）与 P5（位次上限）都是**输入校验**，现在做也能做，
但它们的**合理阈值**取决于修好 P1 之后的实际行为
（现在资格全 UNKNOWN，看不出真实分布）。
建议 P1 → 重跑推荐 → 用真实分布确认阈值 → 再做 P4/P5。

### 依赖 3：P6 需要换数据来源，不在本仓库内

军事学门类要补，只能换更完整的结构化目录（或补一份来源）。
这不是代码改动，且**当前无军队院校计划**，不影响现网结果，可延后。

### 依赖 4：`alembic check` 纳入 CI 必须在 P2 之后

现在把它加进 CI 会**立刻失败**（P2 的漂移 + 70 项 server_default 噪音）。
正确顺序是：先 P2 修漂移 → 再把 `alembic check` 的**噪音过滤**做好（忽略
`modify_default` 与 `alembic_version`）→ 最后才纳入 CI。

### 无依赖：P3（并发 500）可以并行修

P3 是方案版本写入的竞态，与数据、迁移、评分都无关，
可以与其他任何一项并行处理，不受 P1/P2 的顺序约束。
它是三项高危里唯一**不需要重算推荐结果**的 —— 修完只需补一个并发回归测试。

### 依赖 5：所有评分相关修复要一起重算

P1 修好后 ACS/资格判定会变，因此**已生成的推荐会话、方案、体检报告都会过期**。
必须同批完成：修 P1 → 重跑推荐 → 重建方案（重新优化）→ 重跑体检。
否则新旧结果混在同一批数据里，无法判断哪份可信。

---

## 四、建议的执行顺序

| 步骤 | 内容 | 依赖 | 预计影响 |
|---|---|---|---|
| **1** | 写迁移 `0009`：给 `admission_plans` 加 `major_sequence` 列，`special_requirements` 只留真正的特殊要求（P1） | 无 | 需确认重建方式 |
| **2** | 干净重建开发库，消除 5 处结构漂移（P2） | 1 | 清空考生/会话数据 |
| **3** | 改 ETL：序号写入新列，`special_requirements` 留空（P1） | 1、2 | 需重跑 ETL（~9 分钟） |
| **4** | 重跑推荐与会话，确认资格不再是 100% UNKNOWN、保底计数恢复 | 3 | 产出新会话 |
| **5** | 重建方案 + 重跑体检，确认「稳/保」真的出现 | 4 | 覆盖旧方案 |
| **6** | 加 P4（选考科目数=3）与 P5（位次≤达线人数）的校验 + 提示 | 4 | 按真实分布定阈值 |
| **7** | 修 P3：方案版本写入竞态 —— 捕获唯一约束冲突并映射为 409，或把检查与插入放进同一事务 | 无（可并行） | 不需重算推荐 |
| **8** | 过滤 `alembic check` 噪音并纳入 CI（P2 防复发） | 2 | — |
| **9** | 文档更新：P6/P7 已知缺口、P8 环境规避 | — | 无代码风险 |

**回滚点**：步骤 2 之前打一次数据库备份（`pg_dump`），
步骤 3 的 ETL 改动与步骤 1 的迁移可以独立回退。

---

## 五、需要你确认的五件事

1. **P2 的修复方式**：接受**干净重建开发库**（丢失考生档案与推荐会话，参考数据可重载），
   还是只 `downgrade 0006 → upgrade head`（保留 profile/session，只重建 candidate 之后的结构）？

2. **P1 的字段处理**：新增 `major_sequence` 列（推荐，语义干净），
   还是干脆**不存**这个序号（它只用于调试，不参与任何计算）？
   我倾向新增列 —— 序号在排查「同一院校两个相似专业名」时有用。

3. **P4 的严格程度**：选考科目不是恰好 3 门时，
   **直接拒绝（422）**，还是**接受但提示**（允许用户先存草稿）？

4. **P5 的处理方式**：位次超出当年达线人数时，
   **拒绝建档**，还是**接受并在方案里显著提示「该位次超出本年度一段线范围，结果仅作参考」**？

5. **P3 的处理方式**：并发版本冲突时有两条路 ——
   **只捕获异常并映射为 409**（改动最小，明确告诉用户「已被其他地方修改」），
   还是**把版本检查与插入放进同一个事务并加行锁**（从根上消除竞态，改动稍大）。
   我倾向后者：409 只是把错误说清楚了，并没有消除竞态本身。

---

## 六、附：本次可复现的审计命令

```bash
# 代码级
cd backend && pytest -q && ruff check app alembic tests ops && mypy app
cd frontend && npx tsc --noEmit && npx eslint src && npx next build

# 接口级
cd backend && python ops/e2e_check.py http://127.0.0.1:8000

# 数据与算法
python scripts/audit_data_integrity.py
python scripts/audit_algorithm_invariants.py
python scripts/audit_schema_drift.py
python scripts/audit_migration_drift.py
python scripts/audit_discipline_codes.py
python scripts/audit_special_requirements.py
python scripts/audit_unknown_eligibility.py
python scripts/audit_edge_inputs.py
python scripts/audit_boundaries.py
python scripts/audit_concurrency.py
```
