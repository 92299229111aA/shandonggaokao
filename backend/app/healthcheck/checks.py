"""The eleven Health Checks.

Each check is read-only: it inspects the fact snapshot and reports. None of them
may change a selection, an order, or a lock — the candidate decides.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from itertools import pairwise

from app.common.enums import HealthSeverity, RiskBucket
from app.healthcheck.types import (
    LOW_CONFIDENCE_THRESHOLD,
    PLAN_REDUCTION_MIN_HISTORY,
    PLAN_REDUCTION_THRESHOLD,
    SMALL_PLAN_THRESHOLD,
    STALE_SNAPSHOT_MAX_AGE_YEARS,
    CheckResult,
    HealthCheckContext,
    HealthIssue,
    PreferenceFact,
    SchemeFact,
    result,
)

_NAME_DELIMITERS = frozenset("(（[【·-—、,，:：")

RISK_LABELS = {
    RiskBucket.HIGH_REACH.value: "高冲",
    RiskBucket.REACH.value: "冲",
    RiskBucket.BORDER.value: "边界",
    RiskBucket.STABLE.value: "稳",
    RiskBucket.SAFE.value: "保",
}


class EligibilityCheck:
    """Re-confirm 资格 on the finished scheme, including rows added by hand."""

    code = "ELIGIBILITY"
    title = "报考资格"

    def run(self, ctx: HealthCheckContext) -> CheckResult:
        issues: list[HealthIssue] = []
        for item in ctx.items:
            if item.eligibility == "FAIL":
                issues.append(
                    HealthIssue(
                        code="ELIGIBILITY_FAIL",
                        severity=HealthSeverity.ERROR,
                        message=f"第 {item.position} 个志愿（{item.program_label}）报考资格不符，不应提交",
                        position=item.position,
                        detail={"eligibility": item.eligibility, "plan_id": item.plan_id},
                        suggestion="请删除该志愿，或修改选科/硬性条件后重新生成推荐",
                    )
                )
            elif item.eligibility == "UNKNOWN":
                issues.append(
                    HealthIssue(
                        code="ELIGIBILITY_UNKNOWN",
                        severity=HealthSeverity.WARNING,
                        message=(
                            f"第 {item.position} 个志愿（{item.program_label}）"
                            "存在无法自动判定的报考要求，需人工核对招生章程"
                        ),
                        position=item.position,
                        detail={"eligibility": item.eligibility, "plan_id": item.plan_id},
                        suggestion="请查看招生章程确认体检/单科/语种等要求后再决定是否保留",
                    )
                )
        if ctx.recheck_enabled and not issues:
            issues.append(
                HealthIssue(
                    code="ELIGIBILITY_RECHECKED",
                    severity=HealthSeverity.INFO,
                    message="已按当前选科与规则重新核对全部志愿的报考资格，未发现问题",
                    detail={"checked": ctx.item_count},
                )
            )
        return result(self.code, self.title, issues)


class DuplicateCheck:
    """The same 专业+学校 must not be filled twice."""

    code = "DUPLICATE"
    title = "重复志愿"

    def run(self, ctx: HealthCheckContext) -> CheckResult:
        issues: list[HealthIssue] = []
        by_plan: dict[int, list[SchemeFact]] = {}
        for item in ctx.items:
            by_plan.setdefault(item.plan_id, []).append(item)

        for plan_id, group in by_plan.items():
            if len(group) < 2:
                continue
            positions = sorted(item.position for item in group)
            issues.append(
                HealthIssue(
                    code="DUPLICATE_PLAN",
                    severity=HealthSeverity.ERROR,
                    message=(
                        f"同一个志愿（{group[0].program_label}）出现了 {len(group)} 次，"
                        f"位置：{positions}"
                    ),
                    position=positions[0],
                    detail={"plan_id": plan_id, "positions": positions},
                    suggestion="重复志愿会浪费志愿名额，请保留其中一个",
                )
            )
        return result(self.code, self.title, issues)


class ApplicationLimitCheck:
    """Never exceed `admission_cycles.application_limit` (the 96 policy cap)."""

    code = "APPLICATION_LIMIT"
    title = "志愿数量上限"

    def run(self, ctx: HealthCheckContext) -> CheckResult:
        issues: list[HealthIssue] = []
        count = ctx.item_count
        if count > ctx.application_limit:
            issues.append(
                HealthIssue(
                    code="LIMIT_EXCEEDED",
                    severity=HealthSeverity.ERROR,
                    message=(
                        f"当前方案有 {count} 个志愿，超过政策上限 {ctx.application_limit} 个，无法提交"
                    ),
                    detail={"count": count, "application_limit": ctx.application_limit},
                    suggestion=f"请删除至少 {count - ctx.application_limit} 个志愿",
                )
            )
        elif count < ctx.application_limit:
            issues.append(
                HealthIssue(
                    code="LIMIT_UNUSED",
                    severity=HealthSeverity.INFO,
                    message=(
                        f"当前方案有 {count} 个志愿，未填满上限 {ctx.application_limit} 个"
                    ),
                    detail={"count": count, "application_limit": ctx.application_limit},
                    suggestion="平行志愿建议尽量填满，可放宽地域/专业/学费条件增加候选",
                )
            )
        return result(self.code, self.title, issues)


class UnknownRequirementCheck:
    """Unconfirmed requirements must be visible, and must not sit in the safety net."""

    code = "UNKNOWN_REQUIREMENT"
    title = "待确认要求"

    def run(self, ctx: HealthCheckContext) -> CheckResult:
        issues: list[HealthIssue] = []
        for item in ctx.items:
            if item.has_unknown_requirement or item.special_requirements_text:
                text = item.special_requirements_text or item.subject_requirement_text
                issues.append(
                    HealthIssue(
                        code="UNKNOWN_REQUIREMENT",
                        severity=HealthSeverity.WARNING,
                        message=(
                            f"第 {item.position} 个志愿（{item.program_label}）"
                            f"存在需人工确认的要求：{_shorten(text) if text else '详见招生章程'}"
                        ),
                        position=item.position,
                        detail={"plan_id": item.plan_id, "raw_text": text},
                        suggestion="请核对招生章程原文，确认自身条件满足后再保留",
                    )
                )
                # UNKNOWN 项不计入保底
                if item.risk_bucket in {RiskBucket.STABLE.value, RiskBucket.SAFE.value}:
                    issues.append(
                        HealthIssue(
                            code="UNKNOWN_IN_SAFETY_NET",
                            severity=HealthSeverity.WARNING,
                            message=(
                                f"第 {item.position} 个志愿（{item.program_label}）"
                                "存在待确认要求，不能算作保底志愿"
                            ),
                            position=item.position,
                            detail={"plan_id": item.plan_id, "risk_bucket": item.risk_bucket},
                            suggestion="请补充一个要求明确、ACS 较高的志愿作为保底",
                        )
                    )
        return result(self.code, self.title, issues)


class RiskDistributionCheck:
    """Compare the bucket mix with the risk-mode targets (soft) and check the safety net."""

    code = "RISK_DISTRIBUTION"
    title = "风险分布"

    def run(self, ctx: HealthCheckContext) -> CheckResult:
        issues: list[HealthIssue] = []
        actual = Counter(item.risk_bucket or "UNKNOWN" for item in ctx.items)

        for bucket, target in ctx.risk_targets.items():
            if target <= 0:
                continue
            current = actual.get(bucket, 0)
            if current < target:
                label = RISK_LABELS.get(bucket, bucket)
                severity = (
                    HealthSeverity.ERROR
                    if bucket in {RiskBucket.STABLE.value, RiskBucket.SAFE.value} and current == 0
                    else HealthSeverity.WARNING
                )
                issues.append(
                    HealthIssue(
                        code="RISK_TARGET_SHORTFALL",
                        severity=severity,
                        message=(
                            f"“{label}”层目标 {target} 个，实际 {current} 个"
                        ),
                        detail={"risk_bucket": bucket, "target": target, "actual": current},
                        suggestion="可放宽地域/专业/学费限制，或调整风险模式后重新优化",
                    )
                )

        safe_count = actual.get(RiskBucket.STABLE.value, 0) + actual.get(RiskBucket.SAFE.value, 0)
        if safe_count == 0 and ctx.items:
            issues.append(
                HealthIssue(
                    code="NO_SAFETY_NET",
                    severity=HealthSeverity.ERROR,
                    message="方案中没有任何“稳”或“保”的志愿，存在落榜风险",
                    detail={"stable": actual.get(RiskBucket.STABLE.value, 0), "safe": actual.get(RiskBucket.SAFE.value, 0)},
                    suggestion="请至少加入若干 ACS≥60 的志愿作为保底",
                )
            )
        elif safe_count < 10 and ctx.items:
            issues.append(
                HealthIssue(
                    code="THIN_SAFETY_NET",
                    severity=HealthSeverity.WARNING,
                    message=f"“稳/保”志愿只有 {safe_count} 个，保底偏薄",
                    detail={"stable_or_safe": safe_count},
                    suggestion="建议把稳/保志愿增加到 10 个以上",
                )
            )

        required = ctx.preference.min_safe_count
        if required and safe_count < required:
            issues.append(
                HealthIssue(
                    code="MIN_SAFE_COUNT_UNMET",
                    severity=HealthSeverity.ERROR,
                    message=(
                        f"你设置了最低安全数量 {required} 个，当前“稳/保”只有 {safe_count} 个"
                    ),
                    detail={"required": required, "actual": safe_count},
                    suggestion="请补充保底志愿，或降低最低安全数量要求",
                )
            )
        return result(self.code, self.title, issues)


class OrderingCheck:
    """The final order must express real desire: band DESC, then ACS ASC."""

    code = "ORDERING"
    title = "志愿顺序"

    def run(self, ctx: HealthCheckContext) -> CheckResult:
        issues: list[HealthIssue] = []
        items = [item for item in ctx.items if not item.fixed_position]
        if len(items) < 2:
            return result(self.code, self.title, issues)

        # 1) desire_band must be non-increasing
        previous = items[0]
        for current in items[1:]:
            prev_band = _band(previous)
            curr_band = _band(current)
            if curr_band > prev_band:
                issues.append(
                    HealthIssue(
                        code="ORDER_DESIRE_INVERSION",
                        severity=HealthSeverity.WARNING,
                        message=(
                            f"第 {previous.position} 个志愿（偏好 {prev_band} 档）排在第 "
                            f"{current.position} 个志愿（偏好 {curr_band} 档）之前，"
                            "更想去的志愿被排在了后面"
                        ),
                        position=current.position,
                        detail={
                            "earlier_position": previous.position,
                            "earlier_band": prev_band,
                            "later_position": current.position,
                            "later_band": curr_band,
                        },
                        suggestion="平行志愿遵循志愿顺序，越想去越应排前面：请把更想去的志愿上移",
                    )
                )
                break
            previous = current

        # 2) inside one band, harder (lower ACS) must come first
        for earlier, later in pairwise(items):
            if _band(earlier) != _band(later):
                continue
            if earlier.acs is None or later.acs is None:
                continue
            if later.acs < earlier.acs - 1e-9:
                issues.append(
                    HealthIssue(
                        code="ORDER_ACS_INVERSION",
                        severity=HealthSeverity.INFO,
                        message=(
                            f"同一意愿层内第 {later.position} 个志愿（ACS {later.acs:.1f}）"
                            f"比第 {earlier.position} 个（ACS {earlier.acs:.1f}）更难，"
                            "通常应把更难的放在前面"
                        ),
                        position=later.position,
                        detail={
                            "earlier_position": earlier.position,
                            "earlier_acs": earlier.acs,
                            "later_position": later.position,
                            "later_acs": later.acs,
                        },
                        suggestion="同一意愿层内建议把更难的志愿排在前面",
                    )
                )
                break
        return result(self.code, self.title, issues)


class LowConfidenceCheck:
    """Weak historical evidence must be visible, especially in the safety net."""

    code = "LOW_CONFIDENCE"
    title = "数据可信度"

    def run(self, ctx: HealthCheckContext) -> CheckResult:
        issues: list[HealthIssue] = []
        for item in ctx.items:
            if item.confidence is None:
                issues.append(
                    self._issue(
                        item,
                        HealthSeverity.WARNING,
                        f"第 {item.position} 个志愿（{item.program_label}）缺少数据可信度，无法评估历史可靠性",
                        "请确认该专业是否有可用的历史录取数据",
                    )
                )
                continue
            if item.confidence < LOW_CONFIDENCE_THRESHOLD:
                severity = (
                    HealthSeverity.WARNING
                    if item.risk_bucket in {RiskBucket.STABLE.value, RiskBucket.SAFE.value}
                    else HealthSeverity.INFO
                )
                issues.append(
                    self._issue(
                        item,
                        severity,
                        (
                            f"第 {item.position} 个志愿（{item.program_label}）"
                            f"数据可信度仅 {item.confidence:.0f}，ACS 参考价值有限"
                        ),
                        "历史数据不足或波动较大，建议人工核实后再依赖该志愿保底",
                    )
                )
        return result(self.code, self.title, issues)

    @staticmethod
    def _issue(item: SchemeFact, severity: HealthSeverity, message: str, suggestion: str) -> HealthIssue:
        return HealthIssue(
            code="LOW_CONFIDENCE",
            severity=severity,
            message=message,
            position=item.position,
            detail={
                "plan_id": item.plan_id,
                "confidence": item.confidence,
                "risk_bucket": item.risk_bucket,
            },
            suggestion=suggestion,
        )


class SmallPlanCountCheck:
    """A plan with very few seats is a small-sample risk."""

    code = "SMALL_PLAN_COUNT"
    title = "招生人数过少"

    def run(self, ctx: HealthCheckContext) -> CheckResult:
        issues: list[HealthIssue] = []
        for item in ctx.items:
            if item.plan_count <= 0:
                issues.append(
                    HealthIssue(
                        code="PLAN_COUNT_ZERO",
                        severity=HealthSeverity.ERROR,
                        message=f"第 {item.position} 个志愿（{item.program_label}）招生计划为 0，无法填报",
                        position=item.position,
                        detail={"plan_id": item.plan_id, "plan_count": item.plan_count},
                        suggestion="请删除该志愿",
                    )
                )
            elif item.plan_count < SMALL_PLAN_THRESHOLD:
                severity = (
                    HealthSeverity.WARNING
                    if item.risk_bucket in {RiskBucket.STABLE.value, RiskBucket.SAFE.value}
                    else HealthSeverity.INFO
                )
                issues.append(
                    HealthIssue(
                        code="SMALL_PLAN_COUNT",
                        severity=severity,
                        message=(
                            f"第 {item.position} 个志愿（{item.program_label}）"
                            f"只招 {item.plan_count} 人，录取波动大"
                        ),
                        position=item.position,
                        detail={"plan_id": item.plan_id, "plan_count": item.plan_count},
                        suggestion="小计划专业不适合作为保底，请补充招生人数较多的志愿",
                    )
                )
        return result(self.code, self.title, issues)


class PlanReductionCheck:
    """A plan that shrank versus history means tighter competition than ACS implies."""

    code = "PLAN_REDUCTION"
    title = "招生计划缩减"

    def run(self, ctx: HealthCheckContext) -> CheckResult:
        issues: list[HealthIssue] = []
        for item in ctx.items:
            history = [count for count in item.plan_count_history if count > 0]
            # A single history year is not a baseline — its "median" is that year, so any
            # dip would read as a collapse. Only compare against a real trend.
            if len(history) < PLAN_REDUCTION_MIN_HISTORY:
                continue
            median = statistics.median(history)
            if median <= 0:
                continue
            change = (item.plan_count - median) / median
            if change <= -PLAN_REDUCTION_THRESHOLD:
                issues.append(
                    HealthIssue(
                        code="PLAN_REDUCED",
                        severity=HealthSeverity.WARNING,
                        message=(
                            f"第 {item.position} 个志愿（{item.program_label}）今年招 {item.plan_count} 人，"
                            f"比历史中位数 {median:g} 人减少 {abs(change) * 100:.0f}%，竞争预计加剧"
                        ),
                        position=item.position,
                        detail={
                            "plan_id": item.plan_id,
                            "plan_count": item.plan_count,
                            "median_history": median,
                            "change_ratio": round(change, 4),
                        },
                        suggestion="计划缩减会推高录取位次，ACS 可能偏乐观，请谨慎作为保底",
                    )
                )
            elif change >= PLAN_REDUCTION_THRESHOLD:
                issues.append(
                    HealthIssue(
                        code="PLAN_EXPANDED",
                        severity=HealthSeverity.INFO,
                        message=(
                            f"第 {item.position} 个志愿（{item.program_label}）今年扩招 "
                            f"{change * 100:.0f}%，录取位次可能下移"
                        ),
                        position=item.position,
                        detail={"plan_id": item.plan_id, "change_ratio": round(change, 4)},
                    )
                )
        return result(self.code, self.title, issues)


class StaleSnapshotCheck:
    """A scheme built on an old dataset must be re-checked after new data lands."""

    code = "STALE_SNAPSHOT"
    title = "数据时效"

    def run(self, ctx: HealthCheckContext) -> CheckResult:
        issues: list[HealthIssue] = []
        snapshot = ctx.snapshot
        if snapshot is None:
            issues.append(
                HealthIssue(
                    code="SNAPSHOT_MISSING",
                    severity=HealthSeverity.WARNING,
                    message="该方案没有关联任何数据快照，无法判断数据时效",
                    detail={},
                    suggestion="请用最新的数据快照重新生成推荐",
                )
            )
            return result(self.code, self.title, issues)

        latest = ctx.latest_published_snapshot
        if latest is not None and latest.id != snapshot.id:
            issues.append(
                HealthIssue(
                    code="STALE_DATASET",
                    severity=HealthSeverity.WARNING,
                    message=(
                        f"该方案基于数据快照 {snapshot.code}，"
                        f"当前已有更新的快照 {latest.code}，数据可能已过期"
                    ),
                    detail={
                        "scheme_snapshot_id": snapshot.id,
                        "scheme_snapshot_code": snapshot.code,
                        "latest_snapshot_id": latest.id,
                        "latest_snapshot_code": latest.code,
                    },
                    suggestion="请用最新数据快照重新生成推荐，并重新体检",
                )
            )

        if snapshot.status != "PUBLISHED":
            issues.append(
                HealthIssue(
                    code="SNAPSHOT_NOT_PUBLISHED",
                    severity=HealthSeverity.ERROR,
                    message=f"方案使用的数据快照 {snapshot.code} 状态为 {snapshot.status}，不是已发布数据",
                    detail={"status": snapshot.status},
                    suggestion="请使用 PUBLISHED 状态的数据快照重新生成推荐",
                )
            )

        if (
            ctx.target_exam_year is not None
            and snapshot.published_year is not None
            and ctx.target_exam_year - snapshot.published_year > STALE_SNAPSHOT_MAX_AGE_YEARS
        ):
            issues.append(
                HealthIssue(
                    code="SNAPSHOT_TOO_OLD",
                    severity=HealthSeverity.WARNING,
                    message=(
                        f"数据快照年份 {snapshot.published_year} 距目标年份 "
                        f"{ctx.target_exam_year} 已超过 {STALE_SNAPSHOT_MAX_AGE_YEARS} 年"
                    ),
                    detail={
                        "snapshot_year": snapshot.published_year,
                        "target_exam_year": ctx.target_exam_year,
                    },
                    suggestion="请导入最新年份的招生与录取数据",
                )
            )

        if snapshot.is_demo:
            issues.append(
                HealthIssue(
                    code="DEMO_DATA",
                    severity=HealthSeverity.WARNING,
                    message="该方案基于 DEMO 演示数据，不是官方招生数据，禁止用于真实填报决策",
                    detail={"snapshot_code": snapshot.code},
                    suggestion="请在接入正式数据后重新生成方案",
                )
            )
        return result(self.code, self.title, issues)


class HardPreferenceCheck:
    """Guarantee the user's hard boundaries survived editing (spec invariant #6)."""

    code = "HARD_PREFERENCE"
    title = "硬性偏好"

    def run(self, ctx: HealthCheckContext) -> CheckResult:
        issues: list[HealthIssue] = []
        preference = ctx.preference
        for item in ctx.items:
            reasons = _hard_preference_violations(item, preference)
            for reason, detail in reasons:
                issues.append(
                    HealthIssue(
                        code="HARD_PREFERENCE_VIOLATED",
                        severity=HealthSeverity.ERROR,
                        message=f"第 {item.position} 个志愿（{item.program_label}）{reason}",
                        position=item.position,
                        detail={"plan_id": item.plan_id, **detail},
                        suggestion="该志愿违反了你设置的硬性条件，请删除后重新优化",
                    )
                )
        return result(self.code, self.title, issues)


def _hard_preference_violations(
    item: SchemeFact, preference: PreferenceFact
) -> list[tuple[str, dict[str, object]]]:
    reasons: list[tuple[str, dict[str, object]]] = []
    if item.major_id is not None and item.major_id in preference.hard_exclude_major_ids:
        reasons.append(("在你硬性排除的专业列表中", {"major_id": item.major_id}))
    if item.discipline_code and item.discipline_code in preference.hard_exclude_discipline_codes:
        reasons.append(
            ("属于你硬性排除的学科门类", {"discipline_code": item.discipline_code})
        )
    if item.university_id in preference.hard_exclude_university_ids:
        reasons.append(("在你硬性排除的学校列表中", {"university_id": item.university_id}))
    for name in preference.hard_exclude_major_names:
        needle = name.replace(" ", "")
        haystack = (item.plan_name or item.program_label).replace(" ", "")
        if not needle or not haystack.startswith(needle):
            continue
        if haystack == needle or haystack[len(needle) : len(needle) + 1] in _NAME_DELIMITERS:
            reasons.append(("命中你硬性排除的专业名称", {"excluded_name": name}))
            break
    if preference.exclude_cooperative and item.cooperative != "NONE":
        reasons.append(("是中外合作/港澳台合作办学，你已表示不接受", {"cooperative": item.cooperative}))
    if preference.max_tuition is not None and item.tuition_fee > preference.max_tuition:
        reasons.append(
            (
                f"学费 {item.tuition_fee} 元超过你设置的上限 {preference.max_tuition} 元",
                {"tuition_fee": item.tuition_fee, "max_tuition": preference.max_tuition},
            )
        )
    if preference.min_tuition is not None and item.tuition_fee < preference.min_tuition:
        reasons.append(
            (
                f"学费 {item.tuition_fee} 元低于你设置的下限 {preference.min_tuition} 元",
                {"tuition_fee": item.tuition_fee, "min_tuition": preference.min_tuition},
            )
        )
    return reasons


def _band(item: SchemeFact) -> int:
    if item.desire_band is not None:
        return item.desire_band
    score = item.preference_score if item.preference_score is not None else 0.0
    return int(score // 3)


def _shorten(text: str | None, limit: int = 50) -> str | None:
    if not text:
        return None
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed if len(collapsed) <= limit else f"{collapsed[:limit]}…"


# Order matters: the report reads top-down in this sequence.
DEFAULT_CHECKS: tuple[object, ...] = (
    EligibilityCheck(),
    DuplicateCheck(),
    ApplicationLimitCheck(),
    UnknownRequirementCheck(),
    RiskDistributionCheck(),
    OrderingCheck(),
    LowConfidenceCheck(),
    SmallPlanCountCheck(),
    PlanReductionCheck(),
    StaleSnapshotCheck(),
    HardPreferenceCheck(),
)

__all__ = [
    "DEFAULT_CHECKS",
    "ApplicationLimitCheck",
    "DuplicateCheck",
    "EligibilityCheck",
    "HardPreferenceCheck",
    "LowConfidenceCheck",
    "OrderingCheck",
    "PlanReductionCheck",
    "RiskDistributionCheck",
    "SmallPlanCountCheck",
    "StaleSnapshotCheck",
    "UnknownRequirementCheck",
]
