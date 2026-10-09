# 算法说明：ACS / Confidence / Preference / Optimizer

> **第一原则**：ACS 是 **0~100 的录取安全指数**，**不是录取概率**。
> V1 未做概率校准，任何界面、接口、文档都不得出现"录取概率 xx%"。

## 0. 推荐流水线

```
[0]  锁定 Dataset Snapshot / Policy / Model Version
[1]  标准化 CandidateProfile
[2]  Rule Engine 资格过滤          → PASS / FAIL / UNKNOWN
[3]  用户 hard exclude 过滤
[4]  跨年份 Program Series 匹配
[5]  历史位次百分位标准化
[6]  计算 Base ACS
[7]  计算 Data Confidence
[8]  得到最终 ACS  = f(Base ACS, Confidence)
[9]  计算 Preference Score
[10] 构建分层 Candidate Pool
[11] CP-SAT 组合优化
[12] 按真实意愿排序
[13] Health Check
[14] 保存 Scheme Version / 导出
```

## 1. 位次百分位标准化

不同年份考生规模不同，**禁止裸位次跨年直接比较**。对某年最低录取位次 `r_y` 与当年排名人数 `N_y`：

```
q_y = r_y / N_y
q_c = candidate_rank / current_ranked_candidates
d_y = clip( ln(q_y / q_c), -0.30, 0.30 )
```

- `d_y > 0`：考生相对该年最低录取位置更有优势（更安全）。
- `d_y < 0`：偏冲。
- `clip` 抑制极端年份对结果的支配。

## 2. 年份时间衰减

```
w_y = exp(-lambda * age_y),   lambda = ln(2) / 1.5
W_y = w_y / sum(w)
M   = sum(W_y * d_y)
```

即半衰期 1.5 年：越近的年份权重越高。

## 3. 历史基础 ACS

```
HistoryACS = 50 + 35 * tanh(M / 0.07)
```

## 4. 招生计划调整

```
P        = ln( (plan_current + 1) / (median(plan_history) + 1) )
PlanAdj  = 7 * tanh(P / 0.40)
```

计划扩招 → 更安全；计划骤减 → 更危险。

## 5. 趋势调整

对 `ln(q_y)` 按年份做**加权线性回归**，斜率记为 `T`：

```
TrendAdj = 6 * tanh(T / 0.04)
```

`T < 0` 表示近年录取位次越来越靠前（竞争增强）→ `TrendAdj < 0`。

```
BaseACS = clamp(HistoryACS + PlanAdj + TrendAdj, 0, 100)
```

## 6. 数据可信度 Confidence

| 项 | 权重 | 含义 |
|---|---|---|
| 历史覆盖 `Ch` | 0.25 | 3 年及以上趋近 1 |
| 新鲜度 `Cr` | 0.15 | 最近年份完整度 |
| 专业序列匹配 `Cs` | 0.20 | 跨年份专业是否真正可比（`series_match_quality`） |
| 数据源 `Csource` | 0.15 | 官方来源优先 |
| 波动稳定 `Cv` | 0.15 | 历史位次波动越大越低 |
| 招生人数质量 `Cp` | 0.10 | 小样本降低可信度 |

```
C   = 0.25*Ch + 0.15*Cr + 0.20*Cs + 0.15*Csource + 0.15*Cv + 0.10*Cp
```

## 7. 最终 ACS

```
ACS = 50 + (BaseACS - 50) * C
```

即：**数据越不可信，ACS 越向 50 收缩**。这是"不确定就不敢下结论"的体现，
而不是把不确定性伪装成危险分。

## 8. 风险桶

| ACS 区间 | 风险层 | 中文 |
|---|---|---|
| 0 ~ 29 | `HIGH_REACH` | 高冲 |
| 30 ~ 44 | `REACH` | 冲 |
| 45 ~ 59 | `BORDER` | 边界 |
| 60 ~ 79 | `STABLE` | 稳 |
| 80 ~ 100 | `SAFE` | 保 |

边界实现见 `app/common/enums.py::risk_bucket_for`（上界取开区间：29.999 仍属 HIGH_REACH）。

## 9. Preference Score

默认维度权重：

| 维度 | 默认权重 |
|---|---|
| 专业匹配 | 35% |
| 学校匹配 | 15% |
| 地域匹配 | 15% |
| 升学/学校特征 | 10% |
| 职业方向 | 10% |
| 学费 | 10% |
| 个性化偏好 | 5% |

```
Preference = sum(w_i * score_i) / sum(w_i for available dimensions)
```

**关键规则**：缺失维度必须从**分母剔除**，不能按 0 分处理。
例如考生只填了专业与地域偏好，则 `Preference = (0.35*s_major + 0.15*s_loc) / (0.35 + 0.15)`。
标记 `hard_exclude` 的专业/地域/学校在评分前就被排除（`EXCLUDE` 语义）。

## 10. 候选池分层截断

| 风险层 | 候选池上限 |
|---|---|
| `HIGH_REACH` | 120 |
| `REACH` | 120 |
| `BORDER` | 120 |
| `STABLE` | 150 |
| `SAFE` | 150 |

每层按 `Preference Score DESC`、`Confidence DESC` 截取。用户手动锁定的志愿**无条件**加入候选池。

## 11. Portfolio Optimizer（CP-SAT）

二元变量 `x_i ∈ {0,1}` 表示第 i 个候选志愿是否入选；志愿总数上限来自
`admission_cycles.application_limit`（不写死）。

```
MAXIMIZE  PreferenceUtility
        + ConfidenceBonus
        - RiskBucketDeviation
        - UniversityConcentrationPenalty
        - MajorConcentrationPenalty
```

约束：

- `x_i = 0` 对所有 `eligible = FAIL` 或 user `hard_exclude` 的候选（**不可放松**）。
- `sum(x_i) <= application_limit`。
- 风险桶目标是**软约束**（偏离目标计数扣分），最低安全数量（`min_safe_count`）是**硬约束**。
- `UNKNOWN` 项**不计入** SAFE/STABLE 最低安全数量。
- 候选不足时不补垃圾志愿：返回实际数量，并明确提示可放宽哪些条件。

## 12. 默认风险组合

| 模式 | 高冲 | 冲 | 边界 | 稳 | 保 | 最低 ACS≥60 | 最低 ACS≥80 |
|---|---|---|---|---|---|---|---|
| 稳健 STEADY | 5 | 10 | 15 | 36 | 30 | 56 | 20 |
| 均衡 BALANCED | 8 | 17 | 20 | 31 | 20 | 45 | 12 |
| 激进 AGGRESSIVE | 15 | 25 | 20 | 24 | 12 | 32 | 8 |

## 13. 最终排序（与优化分离的第二步）

```
1. 用户手动固定位置 (fixed_position)
2. desire_band DESC          desire_band = floor(PreferenceScore / 3)
3. ACS ASC
4. Preference Score DESC
5. Confidence DESC
```

语义：**最终顺序首先代表"考生真正更想去哪里"**；在喜好相近的同一 `desire_band` 内，
更难的志愿（ACS 更低）排在前面 —— 因为平行志愿是"分数优先、遵循志愿"，
把更想去的放前面、把更难的放在同意愿层内靠前，才符合真实填报策略。

## 14. Health Check 与方案健康分

`health_checks.score`（0~100）只是**产品指标**，用于提示方案质量，**不是录取概率**，
也不得用于任何形式的录取可能性宣传。Health Check 只诊断，**不自动修改方案**。

## 15. 参数版本化

全部算法参数存放在 `recommendation.model_versions.parameters`（JSONB），
与本文档保持同步。改动公式或常量必须：

1. 新增 `model_versions` 记录（不覆盖旧记录）；
2. 同步更新本文档；
3. 更新/新增对应单元测试。
