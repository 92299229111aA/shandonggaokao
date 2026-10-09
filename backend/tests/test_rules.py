"""Phase 3 acceptance: the Eligibility Rule Engine.

Pure unit tests — no database, no HTTP. Every rule and every combination path
from the published truth table:

    任一 BLOCK 规则 FAIL     -> FAIL
    没有 FAIL，但存在 UNKNOWN -> UNKNOWN
    全部明确满足              -> PASS
"""

from __future__ import annotations

import pytest

from app.common.enums import EligibilityStatus, RequirementSeverity, RequirementType, SubjectRuleMode
from app.rules.engine import (
    LanguageRule,
    MedicalRule,
    QualificationRule,
    SexRule,
    SingleSubjectRule,
    SubjectRule,
    TotalScoreRule,
    UnresolvedRequirementRule,
    compare,
)
from app.rules.result import RuleCode
from app.rules.service import RuleEngine, RuleEngineConfig
from app.rules.types import (
    CandidateContext,
    Fact,
    Requirement,
    SubjectRequirement,
    TargetPlan,
    UserHardExcludes,
)

# ---------------- helpers ----------------


def plan(**overrides) -> TargetPlan:
    base = {
        "plan_id": 1,
        "plan_name": "计算机类",
        "university_id": 100,
        "university_name": "演示大学",
        "province_code": "37",
        "city_code": "370200",
        "tuition_fee": 5800,
        "plan_count": 40,
    }
    base.update(overrides)
    return TargetPlan(**base)


def ctx(**overrides) -> CandidateContext:
    base = {
        "rank": 20000,
        "score": 600,
        "current_ranked_candidates": 300000,
        "subjects": frozenset({"PHY", "CHM", "BIO"}),
        "subjects_known": True,
    }
    base.update(overrides)
    return CandidateContext(**base)


def phy_chm_required() -> SubjectRequirement:
    """物理和化学(2门科目考生均须选考方可报考) — one ALL group of two."""
    return SubjectRequirement(
        rule_set_id=1,
        mode=SubjectRuleMode.ALL,
        groups=(("PHY", "CHM"),),
        raw_text="物理和化学(2门科目考生均须选考方可报考)",
    )


# ---------------- 规格要求的关键场景 ----------------


def test_physics_and_chemistry_pass_for_phy_chm_bio() -> None:
    outcome = SubjectRule().evaluate(
        ctx(subjects=frozenset({"PHY", "CHM", "BIO"})), plan(subject_requirement=phy_chm_required())
    )
    assert outcome.status == EligibilityStatus.PASS


def test_physics_and_chemistry_pass_for_phy_chm_geo() -> None:
    outcome = SubjectRule().evaluate(
        ctx(subjects=frozenset({"PHY", "CHM", "GEO"})), plan(subject_requirement=phy_chm_required())
    )
    assert outcome.status == EligibilityStatus.PASS


def test_physics_and_chemistry_fail_for_phy_bio_geo() -> None:
    """规格明确要求：物生地必须 FAIL。"""
    outcome = SubjectRule().evaluate(
        ctx(subjects=frozenset({"PHY", "BIO", "GEO"})), plan(subject_requirement=phy_chm_required())
    )
    assert outcome.status == EligibilityStatus.FAIL
    assert outcome.failures[0].code == RuleCode.SUBJECT_NOT_SATISFIED
    assert "化学" in outcome.failures[0].message or "选科不符" in outcome.failures[0].message


def test_subject_rule_unknown_when_subjects_missing() -> None:
    outcome = SubjectRule().evaluate(
        ctx(subjects=frozenset(), subjects_known=False),
        plan(subject_requirement=phy_chm_required()),
    )
    assert outcome.status == EligibilityStatus.UNKNOWN
    assert outcome.failures == ()


def test_unparsed_subject_requirement_is_unknown_not_pass() -> None:
    """来源不明确的规则必须 UNKNOWN，禁止猜测为 PASS。"""
    requirement = SubjectRequirement(
        rule_set_id=9,
        mode=SubjectRuleMode.ALL,
        groups=(("PHY", "CHM"),),
        raw_text="详见招生章程（未能解析）",
        is_authoritative=False,
    )
    outcome = SubjectRule().evaluate(ctx(), plan(subject_requirement=requirement))
    assert outcome.status == EligibilityStatus.UNKNOWN
    assert outcome.unknowns[0].code == RuleCode.SUBJECT_REQUIREMENT_UNPARSED


def test_no_subject_requirement_is_pass() -> None:
    assert SubjectRule().evaluate(ctx(), plan()).status == EligibilityStatus.PASS


# ---------------- 选科 DNF 结构 ----------------


def test_any_mode_group_is_or() -> None:
    """“物理或历史(任选其一)” = 2 个 ANY 组。"""
    requirement = SubjectRequirement(
        rule_set_id=2,
        mode=SubjectRuleMode.ANY,
        groups=(("PHY",), ("HIS",)),
        raw_text="物理或历史(考生选考其中一门即可报考)",
    )
    assert (
        SubjectRule().evaluate(ctx(subjects=frozenset({"PHY", "BIO", "GEO"})), plan(subject_requirement=requirement)).status
        == EligibilityStatus.PASS
    )
    assert (
        SubjectRule().evaluate(ctx(subjects=frozenset({"HIS", "BIO", "GEO"})), plan(subject_requirement=requirement)).status
        == EligibilityStatus.PASS
    )
    assert (
        SubjectRule().evaluate(ctx(subjects=frozenset({"BIO", "GEO", "POL"})), plan(subject_requirement=requirement)).status
        == EligibilityStatus.FAIL
    )


def test_all_mode_single_group_requires_every_subject() -> None:
    requirement = SubjectRequirement(
        rule_set_id=3, mode=SubjectRuleMode.ALL, groups=(("PHY", "CHM", "BIO"),)
    )
    assert (
        SubjectRule()
        .evaluate(ctx(subjects=frozenset({"PHY", "CHM", "BIO"})), plan(subject_requirement=requirement))
        .status
        == EligibilityStatus.PASS
    )
    assert (
        SubjectRule()
        .evaluate(ctx(subjects=frozenset({"PHY", "CHM", "GEO"})), plan(subject_requirement=requirement))
        .status
        == EligibilityStatus.FAIL
    )


def test_multi_group_dnf_or_of_ands() -> None:
    """(物理+化学) 或 (物理+生物) 任一组满足即可。"""
    requirement = SubjectRequirement(
        rule_set_id=4, mode=SubjectRuleMode.ALL, groups=(("PHY", "CHM"), ("PHY", "BIO"))
    )
    assert (
        SubjectRule()
        .evaluate(ctx(subjects=frozenset({"PHY", "BIO", "GEO"})), plan(subject_requirement=requirement))
        .status
        == EligibilityStatus.PASS
    )
    assert (
        SubjectRule()
        .evaluate(ctx(subjects=frozenset({"CHM", "BIO", "GEO"})), plan(subject_requirement=requirement))
        .status
        == EligibilityStatus.FAIL
    )


# ---------------- 总分 / 单科 ----------------


def test_total_score_below_threshold_fails() -> None:
    requirement = Requirement(
        requirement_type=RequirementType.TOTAL_SCORE.value,
        operator="GTE",
        operand={"min_score": 620},
        raw_text="总分不低于620分",
    )
    outcome = TotalScoreRule().evaluate(ctx(score=600), plan(requirements=(requirement,)))
    assert outcome.status == EligibilityStatus.FAIL
    assert outcome.failures[0].code == RuleCode.TOTAL_SCORE_BELOW


def test_total_score_equal_threshold_passes() -> None:
    requirement = Requirement(
        requirement_type=RequirementType.TOTAL_SCORE.value, operator="GTE", operand={"min_score": 600}
    )
    assert (
        TotalScoreRule().evaluate(ctx(score=600), plan(requirements=(requirement,))).status
        == EligibilityStatus.PASS
    )


def test_total_score_unknown_when_score_missing() -> None:
    requirement = Requirement(
        requirement_type=RequirementType.TOTAL_SCORE.value, operator="GTE", operand={"min_score": 600}
    )
    outcome = TotalScoreRule().evaluate(ctx(score=None), plan(requirements=(requirement,)))
    assert outcome.status == EligibilityStatus.UNKNOWN
    assert outcome.unknowns[0].code == RuleCode.SCORE_UNKNOWN


def test_single_subject_rule_pass_fail_unknown() -> None:
    requirement = Requirement(
        requirement_type=RequirementType.SINGLE_SUBJECT.value,
        operator="GTE",
        operand={"subject_code": "ENG", "min_score": 110},
        raw_text="英语不低于110分",
    )
    target = plan(requirements=(requirement,))

    assert (
        SingleSubjectRule()
        .evaluate(ctx(subject_scores={"ENG": 120}), target)
        .status
        == EligibilityStatus.PASS
    )
    below = SingleSubjectRule().evaluate(ctx(subject_scores={"ENG": 105}), target)
    assert below.status == EligibilityStatus.FAIL
    assert below.failures[0].code == RuleCode.SINGLE_SUBJECT_BELOW

    missing = SingleSubjectRule().evaluate(ctx(subject_scores={}), target)
    assert missing.status == EligibilityStatus.UNKNOWN
    assert missing.unknowns[0].code == RuleCode.SINGLE_SUBJECT_SCORE_UNKNOWN


def test_unsupported_operator_becomes_unknown_not_fail_or_pass() -> None:
    requirement = Requirement(
        requirement_type=RequirementType.TOTAL_SCORE.value, operator="APPROX", operand={"min_score": 600}
    )
    outcome = TotalScoreRule().evaluate(ctx(score=600), plan(requirements=(requirement,)))
    assert outcome.status == EligibilityStatus.UNKNOWN
    assert outcome.unknowns[0].code == RuleCode.REQUIREMENT_MALFORMED


def test_compare_raises_on_unknown_operator() -> None:
    with pytest.raises(ValueError):
        compare(1, "BETWEEN", 2)


# ---------------- 语种 / 性别 ----------------


def test_language_rule() -> None:
    requirement = Requirement(
        requirement_type=RequirementType.LANGUAGE.value, operand={"allowed": ["ENG"]}
    )
    target = plan(requirements=(requirement,))

    assert (
        LanguageRule().evaluate(ctx(foreign_language=Fact.of("ENG")), target).status
        == EligibilityStatus.PASS
    )
    mismatch = LanguageRule().evaluate(ctx(foreign_language=Fact.of("JPN")), target)
    assert mismatch.status == EligibilityStatus.FAIL
    assert mismatch.failures[0].code == RuleCode.FOREIGN_LANGUAGE_MISMATCH

    unknown = LanguageRule().evaluate(ctx(foreign_language=Fact.missing()), target)
    assert unknown.status == EligibilityStatus.UNKNOWN


def test_sex_rule() -> None:
    requirement = Requirement(requirement_type=RequirementType.SEX.value, operand={"allowed": ["MALE"]})
    target = plan(requirements=(requirement,))

    assert SexRule().evaluate(ctx(sex=Fact.of("MALE")), target).status == EligibilityStatus.PASS
    assert SexRule().evaluate(ctx(sex=Fact.of("FEMALE")), target).status == EligibilityStatus.FAIL
    assert SexRule().evaluate(ctx(sex=Fact.missing()), target).status == EligibilityStatus.UNKNOWN
    # an explicit UNKNOWN sex must not be treated as a match
    assert SexRule().evaluate(ctx(sex=Fact.of("UNKNOWN")), target).status == EligibilityStatus.UNKNOWN


# ---------------- 体检 / 资格 ----------------


def test_medical_rule_requires_verified_attribute() -> None:
    requirement = Requirement(
        requirement_type=RequirementType.MEDICAL.value,
        operand={"must_satisfy": ["COLOR_VISION_NORMAL"]},
        raw_text="色觉正常",
    )
    target = plan(requirements=(requirement,))

    assert (
        MedicalRule()
        .evaluate(ctx(attributes={"COLOR_VISION_NORMAL": True}), target)
        .status
        == EligibilityStatus.PASS
    )
    assert (
        MedicalRule()
        .evaluate(ctx(attributes={"COLOR_VISION_NORMAL": False}), target)
        .status
        == EligibilityStatus.FAIL
    )
    unknown = MedicalRule().evaluate(ctx(attributes={}), target)
    assert unknown.status == EligibilityStatus.UNKNOWN
    assert unknown.unknowns[0].code == RuleCode.MEDICAL_UNKNOWN


def test_medical_rule_without_checkable_keys_is_unknown() -> None:
    requirement = Requirement(
        requirement_type=RequirementType.MEDICAL.value, raw_text="身体健康，符合体检指导意见"
    )
    outcome = MedicalRule().evaluate(ctx(), plan(requirements=(requirement,)))
    assert outcome.status == EligibilityStatus.UNKNOWN


def test_qualification_rule() -> None:
    requirement = Requirement(
        requirement_type=RequirementType.QUALIFICATION.value,
        operand={"attribute_key": "RURAL_SPECIAL_QUALIFIED", "expected": True},
        raw_text="高校专项计划资格",
    )
    target = plan(requirements=(requirement,))

    assert (
        QualificationRule()
        .evaluate(ctx(attributes={"RURAL_SPECIAL_QUALIFIED": True}), target)
        .status
        == EligibilityStatus.PASS
    )
    assert (
        QualificationRule()
        .evaluate(ctx(attributes={"RURAL_SPECIAL_QUALIFIED": False}), target)
        .status
        == EligibilityStatus.FAIL
    )
    assert QualificationRule().evaluate(ctx(attributes={}), target).status == EligibilityStatus.UNKNOWN


def test_unresolved_requirement_type_is_unknown() -> None:
    requirement = Requirement(
        requirement_type=RequirementType.OTHER.value, raw_text="需参加学校组织的面试"
    )
    outcome = UnresolvedRequirementRule().evaluate(ctx(), plan(requirements=(requirement,)))
    assert outcome.status == EligibilityStatus.UNKNOWN
    assert outcome.unknowns[0].code == RuleCode.REQUIREMENT_UNRESOLVED


# ---------------- 组合：三态真值表 ----------------


def test_combine_truth_table() -> None:
    from app.rules.result import RuleOutcome, RuleViolation

    def violation(code: RuleCode) -> RuleViolation:
        return RuleViolation(
            code=code,
            message="x",
            requirement_type=RequirementType.OTHER,
            severity=RequirementSeverity.BLOCK,
        )

    from app.rules.result import EligibilityResult

    passing = RuleOutcome.pass_("A")
    unknown = RuleOutcome.unknown("B", violation(RuleCode.REQUIREMENT_UNRESOLVED))
    failing = RuleOutcome.fail("C", violation(RuleCode.SUBJECT_NOT_SATISFIED))

    assert EligibilityResult.combine([passing]).status == EligibilityStatus.PASS
    assert EligibilityResult.combine([passing, unknown]).status == EligibilityStatus.UNKNOWN
    assert EligibilityResult.combine([passing, unknown, failing]).status == EligibilityStatus.FAIL
    assert EligibilityResult.combine([failing, failing]).status == EligibilityStatus.FAIL
    assert (
        EligibilityResult.combine([passing, unknown]).counts_as_safe is False
    ), "UNKNOWN 不能计入保底数量"


def test_fail_is_never_poolable_and_unknown_is_not_safe() -> None:
    from app.rules.result import EligibilityResult

    assert EligibilityResult.combine([]).status == EligibilityStatus.PASS
    fail = RuleEngine().evaluate(
        ctx(subjects=frozenset({"PHY", "BIO", "GEO"})), plan(subject_requirement=phy_chm_required())
    )
    assert fail.can_be_pooled is False
    assert fail.counts_as_safe is False

    unknown_plan = plan(subject_requirement=SubjectRequirement(rule_set_id=1, groups=(), is_authoritative=False))
    unknown = RuleEngine().evaluate(ctx(), unknown_plan)
    assert unknown.status == EligibilityStatus.UNKNOWN
    assert unknown.can_be_pooled is True
    assert unknown.counts_as_safe is False


# ---------------- 用户硬排除 ----------------


def test_user_hard_exclude_major_name() -> None:
    context = ctx(hard_excludes=UserHardExcludes(major_names=("临床医学",)))
    result = RuleEngine().evaluate(context, plan(plan_name="临床医学(5+3一体化)"))
    assert result.status == EligibilityStatus.FAIL
    assert result.hard_excluded is True
    assert result.excluded_by == "USER_HARD_EXCLUDE"
    assert result.can_be_pooled is False


def test_user_hard_exclude_tuition_ceiling() -> None:
    context = ctx(hard_excludes=UserHardExcludes(max_tuition=5000))
    result = RuleEngine().evaluate(context, plan(tuition_fee=5800))
    assert result.status == EligibilityStatus.FAIL
    assert result.hard_excluded is True


def test_user_hard_exclude_cooperative_program() -> None:
    context = ctx(hard_excludes=UserHardExcludes(exclude_cooperative=True))
    result = RuleEngine().evaluate(context, plan(cooperative="CHINESE_FOREIGN"))
    assert result.status == EligibilityStatus.FAIL
    assert RuleEngine().evaluate(context, plan(cooperative="NONE")).status == EligibilityStatus.PASS


def test_user_hard_exclude_never_overridden_by_other_rules() -> None:
    """即使其他规则全部 PASS，硬排除也必须 FAIL（架构不变式 #6）。"""
    context = ctx(hard_excludes=UserHardExcludes(university_ids=frozenset({100})))
    result = RuleEngine().evaluate(context, plan(university_id=100))
    assert result.status == EligibilityStatus.FAIL
    assert result.hard_excluded is True


def test_hard_exclude_beats_unknown() -> None:
    context = ctx(hard_excludes=UserHardExcludes(province_codes=frozenset({"37"})), subjects_known=False)
    result = RuleEngine().evaluate(context, plan(subject_requirement=phy_chm_required()))
    assert result.status == EligibilityStatus.FAIL


# ---------------- engine 级配置 ----------------


def test_missing_subject_requirement_policy() -> None:
    strict = RuleEngine(config=RuleEngineConfig(assume_unrestricted_when_missing=False))
    assert strict.evaluate(ctx(), plan()).status == EligibilityStatus.UNKNOWN
    lenient = RuleEngine(config=RuleEngineConfig(assume_unrestricted_when_missing=True))
    assert lenient.evaluate(ctx(), plan()).status == EligibilityStatus.PASS


def test_special_requirement_text_degrades_to_unknown() -> None:
    target = plan(special_requirements_text="外语语种限英语，且需通过学校面试")
    result = RuleEngine().evaluate(ctx(), target)
    assert result.status == EligibilityStatus.UNKNOWN
    assert any(v.code == RuleCode.SPECIAL_REQUIREMENT_UNVERIFIED for v in result.unknowns)


def test_special_requirement_text_can_be_disabled() -> None:
    target = plan(special_requirements_text="需通过学校面试")
    engine = RuleEngine(config=RuleEngineConfig(flag_special_requirement_text=False))
    assert engine.evaluate(ctx(), target).status == EligibilityStatus.PASS


def test_full_pass_scenario() -> None:
    """明确满足所有条件的志愿必须是 PASS，这样才能计入保底。"""
    target = plan(
        subject_requirement=phy_chm_required(),
        requirements=(
            Requirement(
                requirement_type=RequirementType.TOTAL_SCORE.value, operator="GTE", operand={"min_score": 580}
            ),
            Requirement(
                requirement_type=RequirementType.SINGLE_SUBJECT.value,
                operator="GTE",
                operand={"subject_code": "ENG", "min_score": 100},
            ),
            Requirement(
                requirement_type=RequirementType.MEDICAL.value,
                operand={"must_satisfy": ["COLOR_VISION_NORMAL"]},
            ),
        ),
    )
    context = ctx(subject_scores={"ENG": 120}, attributes={"COLOR_VISION_NORMAL": True})
    result = RuleEngine().evaluate(context, target)
    assert result.status == EligibilityStatus.PASS, result.as_reasons()
    assert result.counts_as_safe is True


def test_evaluate_many_reports_every_plan() -> None:
    engine = RuleEngine()
    plans = [
        plan(plan_id=1, subject_requirement=phy_chm_required()),
        plan(plan_id=2, plan_name="历史学", subject_requirement=SubjectRequirement(rule_set_id=5, groups=(("HIS",),))),
    ]
    results = engine.evaluate_many(ctx(), plans)
    assert [r.status for _, r in results] == [EligibilityStatus.PASS, EligibilityStatus.FAIL]


def test_reasons_are_json_serialisable() -> None:
    import json

    result = RuleEngine().evaluate(
        ctx(subjects=frozenset({"PHY", "BIO", "GEO"})), plan(subject_requirement=phy_chm_required())
    )
    payload = json.dumps(result.as_reasons(), ensure_ascii=False)
    assert "SUBJECT_NOT_SATISFIED" in payload
