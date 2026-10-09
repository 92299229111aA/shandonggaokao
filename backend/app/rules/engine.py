"""Eligibility rules. Each rule answers only: 能不能报 (PASS / FAIL / UNKNOWN).

Rules are pure functions of (CandidateContext, TargetPlan) -> RuleOutcome.
No I/O, no ORM, no HTTP, so every branch is unit-testable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from app.common.enums import RequirementSeverity, RequirementType
from app.rules.result import RuleCode, RuleOutcome, RuleViolation
from app.rules.types import CandidateContext, Requirement, SubjectRequirement, TargetPlan

_DEFAULT_SCORE_OPERATOR = "GTE"


def _severity(raw: str) -> RequirementSeverity:
    try:
        return RequirementSeverity(raw)
    except ValueError:
        # Unknown severity is treated as blocking, never as a free pass.
        return RequirementSeverity.BLOCK


def _subject_text(code: str) -> str:
    return SUBJECT_NAMES.get(code, code)


SUBJECT_NAMES: dict[str, str] = {
    "PHY": "物理",
    "CHM": "化学",
    "BIO": "生物",
    "POL": "政治",
    "HIS": "历史",
    "GEO": "地理",
    "TEC": "技术",
    "ENG": "英语",
    "JPN": "日语",
    "RUS": "俄语",
    "GER": "德语",
    "FRA": "法语",
    "SPA": "西班牙语",
}


class SubjectRule:
    """选科要求, evaluated as DNF.

    `ALL` mode inside a group means "every listed subject must be selected";
    `ANY` mode means "at least one of the listed subjects". Groups are OR-ed.
    An omitted 选科 requirement is *not* treated as satisfiable — see
    `assume_unrestricted_when_missing` in RuleEngineConfig.
    """

    code = "SUBJECT"

    def evaluate(self, ctx: CandidateContext, plan: TargetPlan) -> RuleOutcome:
        requirement = plan.subject_requirement
        if requirement is None or (not requirement.groups and requirement.is_authoritative):
            # No restriction row at all, or an authoritatively "不限" requirement
            return RuleOutcome.pass_(self.code)
        if not requirement.is_authoritative:
            # Checked before the empty-group shortcut: an unparsed requirement
            # must never be read as "no restriction".
            return RuleOutcome.unknown(
                self.code,
                RuleViolation(
                    code=RuleCode.SUBJECT_REQUIREMENT_UNPARSED,
                    message=(
                        f"选科要求“{requirement.raw_text or '未提供原文'}”无法确定性解析，"
                        "需人工核对后再填报"
                    ),
                    requirement_type=RequirementType.SUBJECT,
                    severity=RequirementSeverity.BLOCK,
                    detail={"rule_set_id": requirement.rule_set_id, "raw_text": requirement.raw_text},
                ),
            )
        if not ctx.subjects_known:
            return RuleOutcome.unknown(
                self.code,
                RuleViolation(
                    code=RuleCode.SUBJECTS_UNKNOWN,
                    message="缺少考生选科信息，无法判断选科要求",
                    requirement_type=RequirementType.SUBJECT,
                    severity=RequirementSeverity.BLOCK,
                ),
            )

        satisfied = False
        for group in requirement.groups:
            group_ok = self._group_satisfied(group, requirement, ctx)
            if group_ok is None:  # group itself unparseable -> cannot conclude PASS
                return RuleOutcome.unknown(
                    self.code,
                    RuleViolation(
                        code=RuleCode.SUBJECT_REQUIREMENT_UNPARSED,
                        message=f"选科要求分组 {sorted(group)} 无法判定",
                        requirement_type=RequirementType.SUBJECT,
                        severity=RequirementSeverity.BLOCK,
                        detail={"group": list(group)},
                    ),
                )
            if group_ok:
                satisfied = True
                break

        if satisfied:
            return RuleOutcome.pass_(self.code)

        return RuleOutcome.fail(
            self.code,
            RuleViolation(
                code=RuleCode.SUBJECT_NOT_SATISFIED,
                message=(
                    f"选科不符：要求 {' 或 '.join(requirement.alternative_texts())}，"
                    f"考生选考 {'、'.join(_subject_text(s) for s in sorted(ctx.subjects))}"
                ),
                requirement_type=RequirementType.SUBJECT,
                severity=RequirementSeverity.BLOCK,
                detail={
                    "required_groups": [list(g) for g in requirement.groups],
                    "candidate_subjects": sorted(ctx.subjects),
                },
            ),
        )

    @staticmethod
    def _group_satisfied(
        group: tuple[str, ...], requirement: SubjectRequirement, ctx: CandidateContext
    ) -> bool | None:
        if not group:
            return True
        mode = requirement.mode
        if mode == "ANY" and len(group) == 1:
            # A single-subject ANY group and an ALL group of one are equivalent
            return group[0] in ctx.subjects
        if mode == "ANY":
            return any(code in ctx.subjects for code in group)
        if mode == "ALL":
            return all(code in ctx.subjects for code in group)
        return None


class TotalScoreRule:
    """总分要求 (usually the 本科线 / 一段线 style gate)."""

    code = "TOTAL_SCORE"

    def evaluate(self, ctx: CandidateContext, plan: TargetPlan) -> RuleOutcome:
        requirements = plan.requirements_of(RequirementType.TOTAL_SCORE)
        if not requirements:
            return RuleOutcome.pass_(self.code)

        unknown: list[RuleViolation] = []
        for requirement in requirements:
            threshold = requirement.number("min_score")
            if threshold is None:
                threshold = requirement.number("value")
            if threshold is None:
                unknown.append(self._malformed(requirement))
                continue
            if ctx.score is None:
                if _severity(requirement.severity) is RequirementSeverity.BLOCK:
                    unknown.append(self._score_unknown(requirement, threshold))
                continue
            try:
                ok = compare(ctx.score, requirement.operator or _DEFAULT_SCORE_OPERATOR, threshold)
            except ValueError:
                unknown.append(self._malformed(requirement))
                continue
            if not ok:
                return RuleOutcome.fail(
                    self.code,
                    RuleViolation(
                        code=RuleCode.TOTAL_SCORE_BELOW,
                        message=f"总分 {ctx.score} 不满足要求“{requirement.raw_text or threshold}”",
                        requirement_type=RequirementType.TOTAL_SCORE,
                        severity=_severity(requirement.severity),
                        detail={"score": ctx.score, "threshold": threshold},
                    ),
                )
        if unknown:
            return RuleOutcome.unknown(self.code, *unknown)
        return RuleOutcome.pass_(self.code)

    @staticmethod
    def _score_unknown(requirement: Requirement, threshold: float) -> RuleViolation:
        return RuleViolation(
            code=RuleCode.SCORE_UNKNOWN,
            message=f"缺少高考总分，无法判断总分要求（要求 ≥ {threshold:g}）",
            requirement_type=RequirementType.TOTAL_SCORE,
            severity=RequirementSeverity.BLOCK,
            detail={"threshold": threshold, "raw_text": requirement.raw_text},
        )

    @staticmethod
    def _malformed(requirement: Requirement) -> RuleViolation:
        return RuleViolation(
            code=RuleCode.REQUIREMENT_MALFORMED,
            message="总分要求缺少可比对的阈值，需要人工核对",
            requirement_type=RequirementType.TOTAL_SCORE,
            severity=RequirementSeverity.BLOCK,
            detail={"operand": dict(requirement.operand or {}), "raw_text": requirement.raw_text},
        )


class SingleSubjectRule:
    """单科成绩要求, e.g. 英语 ≥ 110 或 数学 ≥ 120."""

    code = "SINGLE_SUBJECT"

    def evaluate(self, ctx: CandidateContext, plan: TargetPlan) -> RuleOutcome:
        requirements = plan.requirements_of(RequirementType.SINGLE_SUBJECT)
        if not requirements:
            return RuleOutcome.pass_(self.code)

        unknown: list[RuleViolation] = []
        for requirement in requirements:
            subject = requirement.text("subject_code") or requirement.text("subject")
            threshold = requirement.number("min_score")
            if threshold is None:
                threshold = requirement.number("value")
            if not subject or threshold is None:
                unknown.append(
                    RuleViolation(
                        code=RuleCode.REQUIREMENT_MALFORMED,
                        message="单科要求缺少科目或阈值，需要人工核对",
                        requirement_type=RequirementType.SINGLE_SUBJECT,
                        severity=RequirementSeverity.BLOCK,
                        detail={"operand": dict(requirement.operand or {}), "raw_text": requirement.raw_text},
                    )
                )
                continue

            actual = ctx.subject_scores.get(subject)
            if actual is None:
                unknown.append(
                    RuleViolation(
                        code=RuleCode.SINGLE_SUBJECT_SCORE_UNKNOWN,
                        message=(
                            f"缺少{_subject_text(subject)}单科成绩，"
                            f"无法判断单科要求（要求 ≥ {threshold:g}）"
                        ),
                        requirement_type=RequirementType.SINGLE_SUBJECT,
                        severity=RequirementSeverity.BLOCK,
                        detail={"subject_code": subject, "threshold": threshold},
                    )
                )
                continue

            try:
                ok = compare(actual, requirement.operator or _DEFAULT_SCORE_OPERATOR, threshold)
            except ValueError:
                unknown.append(
                    RuleViolation(
                        code=RuleCode.REQUIREMENT_MALFORMED,
                        message=f"单科要求的比较方式 {requirement.operator!r} 无法识别，需要人工核对",
                        requirement_type=RequirementType.SINGLE_SUBJECT,
                        severity=RequirementSeverity.BLOCK,
                        detail={"operator": requirement.operator},
                    )
                )
                continue
            if not ok:
                return RuleOutcome.fail(
                    self.code,
                    RuleViolation(
                        code=RuleCode.SINGLE_SUBJECT_BELOW,
                        message=(
                            f"{_subject_text(subject)} {actual} 分不满足单科要求"
                            f"（要求 ≥ {threshold:g}）"
                        ),
                        requirement_type=RequirementType.SINGLE_SUBJECT,
                        severity=_severity(requirement.severity),
                        detail={"subject_code": subject, "actual": actual, "threshold": threshold},
                    ),
                )
        if unknown:
            return RuleOutcome.unknown(self.code, *unknown)
        return RuleOutcome.pass_(self.code)


class LanguageRule:
    """外语语种要求 (e.g. 仅限英语考生)."""

    code = "FOREIGN_LANGUAGE"

    def evaluate(self, ctx: CandidateContext, plan: TargetPlan) -> RuleOutcome:
        requirements = plan.requirements_of(RequirementType.LANGUAGE)
        if not requirements:
            return RuleOutcome.pass_(self.code)

        unknown: list[RuleViolation] = []
        for requirement in requirements:
            allowed = requirement.list_("allowed") or requirement.list_("languages")
            if not allowed:
                unknown.append(
                    RuleViolation(
                        code=RuleCode.REQUIREMENT_MALFORMED,
                        message="外语语种要求未列出可接受语种，需要人工核对",
                        requirement_type=RequirementType.LANGUAGE,
                        severity=RequirementSeverity.BLOCK,
                        detail={"operand": dict(requirement.operand or {}), "raw_text": requirement.raw_text},
                    )
                )
                continue

            if not ctx.foreign_language.known:
                unknown.append(
                    RuleViolation(
                        code=RuleCode.FOREIGN_LANGUAGE_UNKNOWN,
                        message=f"缺少外语语种信息，无法判断语种要求（限 {'/'.join(allowed)}）",
                        requirement_type=RequirementType.LANGUAGE,
                        severity=RequirementSeverity.BLOCK,
                        detail={"allowed": list(allowed)},
                    )
                )
                continue

            if ctx.foreign_language.value not in allowed:
                return RuleOutcome.fail(
                    self.code,
                    RuleViolation(
                        code=RuleCode.FOREIGN_LANGUAGE_MISMATCH,
                        message=(
                            f"外语语种不符：要求 {'/'.join(allowed)}，"
                            f"考生为 {ctx.foreign_language.value}"
                        ),
                        requirement_type=RequirementType.LANGUAGE,
                        severity=_severity(requirement.severity),
                        detail={"allowed": list(allowed), "actual": ctx.foreign_language.value},
                    ),
                )
        if unknown:
            return RuleOutcome.unknown(self.code, *unknown)
        return RuleOutcome.pass_(self.code)


class SexRule:
    """性别要求 (军警、航海等专业常见)."""

    code = "SEX"

    def evaluate(self, ctx: CandidateContext, plan: TargetPlan) -> RuleOutcome:
        requirements = plan.requirements_of(RequirementType.SEX)
        if not requirements:
            return RuleOutcome.pass_(self.code)

        unknown: list[RuleViolation] = []
        for requirement in requirements:
            allowed = requirement.list_("allowed") or requirement.list_("sex")
            if not allowed:
                unknown.append(
                    RuleViolation(
                        code=RuleCode.REQUIREMENT_MALFORMED,
                        message="性别要求未列出可接受取值，需要人工核对",
                        requirement_type=RequirementType.SEX,
                        severity=RequirementSeverity.BLOCK,
                        detail={"raw_text": requirement.raw_text},
                    )
                )
                continue

            if not ctx.sex.known or ctx.sex.value == "UNKNOWN":
                unknown.append(
                    RuleViolation(
                        code=RuleCode.SEX_UNKNOWN,
                        message=f"缺少考生性别信息，无法判断性别要求（限 {'/'.join(allowed)}）",
                        requirement_type=RequirementType.SEX,
                        severity=RequirementSeverity.BLOCK,
                        detail={"allowed": list(allowed)},
                    )
                )
                continue

            if ctx.sex.value not in allowed:
                return RuleOutcome.fail(
                    self.code,
                    RuleViolation(
                        code=RuleCode.SEX_MISMATCH,
                        message=f"性别不符：要求 {'/'.join(allowed)}",
                        requirement_type=RequirementType.SEX,
                        severity=_severity(requirement.severity),
                        detail={"allowed": list(allowed), "actual": ctx.sex.value},
                    ),
                )
        if unknown:
            return RuleOutcome.unknown(self.code, *unknown)
        return RuleOutcome.pass_(self.code)


class MedicalRule:
    """体检/身体条件要求.

    `operand.must_satisfy` lists candidate attribute keys that must be true
    (e.g. `COLOR_VISION_NORMAL`). Unverified attributes are UNKNOWN, never PASS.
    """

    code = "MEDICAL"

    def evaluate(self, ctx: CandidateContext, plan: TargetPlan) -> RuleOutcome:
        requirements = plan.requirements_of(RequirementType.MEDICAL)
        if not requirements:
            return RuleOutcome.pass_(self.code)

        unknown: list[RuleViolation] = []
        for requirement in requirements:
            keys = requirement.list_("must_satisfy") or requirement.list_("attribute_keys")
            if not keys:
                unknown.append(
                    RuleViolation(
                        code=RuleCode.MEDICAL_UNKNOWN,
                        message=(
                            f"体检要求“{requirement.raw_text or '未提供原文'}”无法确定性核对，"
                            "需人工确认体检结论"
                        ),
                        requirement_type=RequirementType.MEDICAL,
                        severity=_severity(requirement.severity),
                        detail={"raw_text": requirement.raw_text},
                    )
                )
                continue

            for key in keys:
                fact = ctx.attribute(key)
                if not fact.known:
                    unknown.append(
                        RuleViolation(
                            code=RuleCode.MEDICAL_UNKNOWN,
                            message=(
                                f"缺少体检信息“{key}”，无法判断体检要求"
                                f"（{requirement.raw_text or '见招生章程'}）"
                            ),
                            requirement_type=RequirementType.MEDICAL,
                            severity=RequirementSeverity.BLOCK,
                            detail={"attribute_key": key, "raw_text": requirement.raw_text},
                        )
                    )
                    continue
                if fact.value is not True:
                    return RuleOutcome.fail(
                        self.code,
                        RuleViolation(
                            code=RuleCode.MEDICAL_UNSATISFIED,
                            message=(
                                f"体检不符：{key} 不满足"
                                f"（{requirement.raw_text or '见招生章程'}）"
                            ),
                            requirement_type=RequirementType.MEDICAL,
                            severity=_severity(requirement.severity),
                            detail={"attribute_key": key, "actual": fact.value},
                        ),
                    )
        if unknown:
            return RuleOutcome.unknown(self.code, *unknown)
        return RuleOutcome.pass_(self.code)


class QualificationRule:
    """报考资格要求 (户籍、专项计划资格、面试/政审等)."""

    code = "QUALIFICATION"

    def evaluate(self, ctx: CandidateContext, plan: TargetPlan) -> RuleOutcome:
        requirements = plan.requirements_of(RequirementType.QUALIFICATION)
        if not requirements:
            return RuleOutcome.pass_(self.code)

        unknown: list[RuleViolation] = []
        for requirement in requirements:
            key = requirement.text("attribute_key") or requirement.text("key")
            expected = requirement.operand.get("expected") if requirement.operand else None
            if not key or expected is None:
                unknown.append(
                    RuleViolation(
                        code=RuleCode.QUALIFICATION_UNKNOWN,
                        message=(
                            f"资格要求“{requirement.raw_text or '未提供原文'}”无法确定性核对，"
                            "需人工确认报考资格"
                        ),
                        requirement_type=RequirementType.QUALIFICATION,
                        severity=_severity(requirement.severity),
                        detail={"raw_text": requirement.raw_text},
                    )
                )
                continue

            fact = ctx.attribute(key)
            if not fact.known:
                unknown.append(
                    RuleViolation(
                        code=RuleCode.QUALIFICATION_UNKNOWN,
                        message=f"缺少资格信息“{key}”，无法判断报考资格要求",
                        requirement_type=RequirementType.QUALIFICATION,
                        severity=RequirementSeverity.BLOCK,
                        detail={"attribute_key": key, "expected": expected},
                    )
                )
                continue

            if not compare_eq(fact.value, expected):
                return RuleOutcome.fail(
                    self.code,
                    RuleViolation(
                        code=RuleCode.QUALIFICATION_MISSING,
                        message=f"不满足报考资格要求：{key} 需为 {expected}",
                        requirement_type=RequirementType.QUALIFICATION,
                        severity=_severity(requirement.severity),
                        detail={"attribute_key": key, "expected": expected, "actual": fact.value},
                    ),
                )
        if unknown:
            return RuleOutcome.unknown(self.code, *unknown)
        return RuleOutcome.pass_(self.code)


class UnresolvedRequirementRule:
    """Catch-all for requirement rows no dedicated rule consumed.

    This is what keeps "we don't know" from silently becoming "fine".
    """

    code = "UNRESOLVED"
    handled_types = frozenset(
        {
            RequirementType.SUBJECT.value,
            RequirementType.TOTAL_SCORE.value,
            RequirementType.SINGLE_SUBJECT.value,
            RequirementType.LANGUAGE.value,
            RequirementType.SEX.value,
            RequirementType.MEDICAL.value,
            RequirementType.QUALIFICATION.value,
        }
    )

    def evaluate(self, ctx: CandidateContext, plan: TargetPlan) -> RuleOutcome:
        leftovers = tuple(r for r in plan.requirements if r.requirement_type not in self.handled_types)
        if not leftovers:
            return RuleOutcome.pass_(self.code)
        valid = {item.value for item in RequirementType}
        return RuleOutcome.unknown(
            self.code,
            *(
                RuleViolation(
                    code=RuleCode.REQUIREMENT_UNRESOLVED,
                    message=(
                        f"存在无法自动判定的要求（{requirement.requirement_type}）："
                        f"{requirement.raw_text or '未提供原文'}，需人工确认"
                    ),
                    requirement_type=(
                        RequirementType(requirement.requirement_type)
                        if requirement.requirement_type in valid
                        else RequirementType.OTHER
                    ),
                    severity=_severity(requirement.severity),
                    detail={"requirement_id": requirement.requirement_id},
                )
                for requirement in leftovers
            ),
        )


class SpecialRequirementTextRule:
    """Unstructured 招生章程 text attached to a plan.

    Free text alone is not a deterministic gate: it lowers confidence to UNKNOWN
    so the candidate sees "需要自己核对章程", rather than being told PASS.
    """

    code = "SPECIAL_TEXT"

    def evaluate(self, ctx: CandidateContext, plan: TargetPlan) -> RuleOutcome:
        text = (plan.special_requirements_text or "").strip()
        if not text:
            return RuleOutcome.pass_(self.code)
        return RuleOutcome.unknown(
            self.code,
            RuleViolation(
                code=RuleCode.SPECIAL_REQUIREMENT_UNVERIFIED,
                message=f"该专业另有招生章程要求需自行核对：{_shorten(text)}",
                requirement_type=RequirementType.OTHER,
                severity=RequirementSeverity.WARN,
                detail={"special_requirements": text},
            ),
        )


def _shorten(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…"


def compare(actual: float, operator: str, threshold: float) -> bool:
    """Numeric comparison with the operators used in `admission_requirements`.

    Raises ValueError for an unrecognised operator: an unknown operator must
    become UNKNOWN, never a silent False (which the caller would read as FAIL)
    nor a silent True.
    """
    op = (operator or _DEFAULT_SCORE_OPERATOR).upper()
    if op in {"GTE", "GE", ">="}:
        return actual >= threshold
    if op in {"GT", ">"}:
        return actual > threshold
    if op in {"LTE", "LE", "<="}:
        return actual <= threshold
    if op in {"LT", "<"}:
        return actual < threshold
    if op in {"EQ", "=", "=="}:
        return actual == threshold
    if op in {"NE", "!=", "<>"}:
        return actual != threshold
    raise ValueError(f"unsupported comparison operator: {operator!r}")


def compare_eq(actual: object, expected: object) -> bool:
    return str(actual).strip().upper() == str(expected).strip().upper()


RuleFn = Callable[[CandidateContext, TargetPlan], RuleOutcome]


class Rule(Protocol):
    """A rule instance: a stable `code` plus a pure evaluate method."""

    code: str

    def evaluate(self, ctx: CandidateContext, plan: TargetPlan) -> RuleOutcome: ...


DEFAULT_RULES: tuple[Rule, ...] = (
    SubjectRule(),
    TotalScoreRule(),
    SingleSubjectRule(),
    LanguageRule(),
    SexRule(),
    MedicalRule(),
    QualificationRule(),
    UnresolvedRequirementRule(),
    SpecialRequirementTextRule(),
)


__all__ = [
    "DEFAULT_RULES",
    "LanguageRule",
    "MedicalRule",
    "QualificationRule",
    "Rule",
    "RuleFn",
    "SexRule",
    "SingleSubjectRule",
    "SubjectRule",
    "TotalScoreRule",
    "UnresolvedRequirementRule",
    "compare",
    "compare_eq",
]
