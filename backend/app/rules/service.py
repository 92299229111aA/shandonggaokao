"""Rule Engine entry point.

`RuleEngine.evaluate` is the only thing the pipeline calls. Hard excludes are
applied here too, because "user said never" outranks any rule verdict
(architecture invariant #6).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.common.enums import EligibilityStatus, RequirementSeverity, RequirementType
from app.rules.engine import DEFAULT_RULES, Rule
from app.rules.result import EligibilityResult, RuleCode, RuleOutcome, RuleViolation
from app.rules.types import CandidateContext, TargetPlan


@dataclass(frozen=True, slots=True)
class RuleEngineConfig:
    """Switches that must be explicit rather than implicit.

    `assume_unrestricted_when_missing`: when a plan has no 选科要求 row at all.
    Default True because Shandong publishes "不限" as the absence of a
    restriction; set False for datasets where a missing row means "not imported
    yet" — then the plan degrades to UNKNOWN instead of PASS.
    """

    assume_unrestricted_when_missing: bool = True
    flag_special_requirement_text: bool = True


class HardExcludeFilter:
    """User hard blocks. This is a filter, not a score: it returns a verdict."""

    code = "HARD_EXCLUDE"

    def evaluate(self, ctx: CandidateContext, plan: TargetPlan) -> RuleOutcome:
        hard = ctx.hard_excludes
        reasons: list[str] = []

        if plan.university_id in hard.university_ids:
            reasons.append("学校在硬性排除列表中")
        if hard.province_codes and plan.province_code in hard.province_codes:
            reasons.append("地域（省）在硬性排除列表中")
        if hard.city_codes and plan.city_code and plan.city_code in hard.city_codes:
            reasons.append("地域（市）在硬性排除列表中")
        if hard.exclude_cooperative and plan.cooperative != "NONE":
            reasons.append("不接受中外合作/港澳台合作办学")
        if hard.max_tuition is not None and plan.tuition_fee > hard.max_tuition:
            reasons.append(f"学费 {plan.tuition_fee} 元超过上限 {hard.max_tuition} 元")
        if hard.discipline_codes and plan.discipline_code in hard.discipline_codes:
            reasons.append("学科门类在硬性排除列表中")
        if hard.major_ids and plan.major_id is not None and plan.major_id in hard.major_ids:
            reasons.append("专业在硬性排除列表中")
        if hard.major_names and plan.matches_major_name(hard.major_names):
            reasons.append("专业名称匹配用户明确排除的专业")

        if not reasons:
            return RuleOutcome.pass_(self.code)

        # Reported as FAIL so the candidate is never recommended it again.
        return RuleOutcome.fail(
            self.code,
            RuleViolation(
                code=RuleCode.USER_HARD_EXCLUDE,
                message="；".join(reasons),
                requirement_type=RequirementType.PREFERENCE,
                severity=RequirementSeverity.BLOCK,
                detail={"reasons": reasons},
            ),
        )


class RuleEngine:
    def __init__(
        self,
        *,
        rule_list: tuple[Rule, ...] | None = None,
        config: RuleEngineConfig | None = None,
    ) -> None:
        # Keyword-only on purpose: both arguments are optional and silently
        # accepting the config in the rules slot would disable every rule.
        self.rules: tuple[Rule, ...] = tuple(rule_list) if rule_list is not None else DEFAULT_RULES
        self.config: RuleEngineConfig = config if config is not None else RuleEngineConfig()

    def evaluate(self, ctx: CandidateContext, plan: TargetPlan) -> EligibilityResult:
        outcomes: list[RuleOutcome] = []

        # 1. user hard excludes first: never recommend what the user refused
        outcomes.append(HardExcludeFilter().evaluate(ctx, plan))

        # 2. 选科: a missing requirement row is only PASS under an explicit policy
        if plan.subject_requirement is None and not self.config.assume_unrestricted_when_missing:
            outcomes.append(
                RuleOutcome.unknown(
                    "SUBJECT",
                    RuleViolation(
                        code=RuleCode.SUBJECT_REQUIREMENT_UNPARSED,
                        message="该招生计划缺少选科要求数据，无法确认是否可以填报",
                        requirement_type=RequirementType.SUBJECT,
                        severity=RequirementSeverity.BLOCK,
                        detail={"plan_id": plan.plan_id},
                    ),
                )
            )

        # 3. deterministic rules
        rules = self.rules
        if not self.config.flag_special_requirement_text:
            rules = tuple(rule for rule in rules if rule.code != "SPECIAL_TEXT")
        for rule in rules:
            outcomes.append(rule.evaluate(ctx, plan))

        result = EligibilityResult.combine(outcomes)
        if any(str(v.code) == RuleCode.USER_HARD_EXCLUDE for v in result.failures):
            return EligibilityResult(
                status=EligibilityStatus.FAIL,
                failures=result.failures,
                unknowns=result.unknowns,
                warnings=result.warnings,
                hard_excluded=True,
                excluded_by="USER_HARD_EXCLUDE",
            )
        return result

    def evaluate_many(
        self, ctx: CandidateContext, plans: list[TargetPlan]
    ) -> list[tuple[TargetPlan, EligibilityResult]]:
        return [(plan, self.evaluate(ctx, plan)) for plan in plans]


__all__ = ["HardExcludeFilter", "RuleEngine", "RuleEngineConfig"]
