"""Map ORM rows onto the Rule Engine's pure input dataclasses.

This is the only place where the Rule Engine touches persistence concerns, and it
does so one-way: ORM rows in, plain dataclasses out.
"""

from __future__ import annotations

from typing import Any

from app.admission.models import AdmissionRequirement
from app.candidate.models import Attribute, PreferenceProfile, Profile, SubjectScore
from app.common.enums import SubjectRuleMode
from app.reference.models import Major
from app.rules.types import (
    CandidateContext,
    Fact,
    Requirement,
    SubjectRequirement,
    TargetPlan,
    UserHardExcludes,
)


def build_subject_requirement(
    *,
    rule_set_id: int | None,
    mode: str | None,
    groups: list[list[str]] | None,
    raw_text: str | None,
    is_authoritative: bool | None,
) -> SubjectRequirement | None:
    if rule_set_id is None:
        return None
    try:
        parsed_mode = SubjectRuleMode(mode or "ALL")
    except ValueError:
        # Unrecognised mode must not be interpreted as the permissive one.
        parsed_mode = SubjectRuleMode.ALL
        is_authoritative = False
    return SubjectRequirement(
        rule_set_id=rule_set_id,
        mode=parsed_mode,
        groups=tuple(tuple(group) for group in (groups or [])),
        raw_text=raw_text,
        is_authoritative=bool(is_authoritative) if is_authoritative is not None else True,
    )


def build_requirement(row: AdmissionRequirement) -> Requirement:
    return Requirement(
        requirement_type=str(row.requirement_type),
        operator=str(row.operator or "EQ"),
        operand=row.operand,
        severity=str(row.severity or "BLOCK"),
        is_deterministic=bool(row.is_deterministic),
        raw_text=row.raw_text,
        requirement_id=row.id,
    )


def build_target_plan(
    *,
    plan_row: dict[str, Any],
    subject_groups: list[list[str]] | None = None,
    requirements: list[AdmissionRequirement] | None = None,
    major: Major | None = None,
) -> TargetPlan:
    """`plan_row` is the joined dict produced by PlanRepository.base_query()."""
    subject_requirement = build_subject_requirement(
        rule_set_id=plan_row.get("subject_rule_set_id"),
        mode=plan_row.get("subject_rule_mode"),
        groups=subject_groups,
        raw_text=plan_row.get("subject_rule_raw_text"),
        is_authoritative=plan_row.get("subject_rule_is_authoritative"),
    )
    return TargetPlan(
        plan_id=plan_row["plan_id"],
        plan_name=plan_row["plan_name"],
        university_id=plan_row["university_id"],
        university_name=plan_row["university_name"],
        province_code=plan_row.get("province_code") or "",
        city_code=plan_row.get("city_code"),
        cooperative=plan_row.get("cooperative") or "NONE",
        tuition_fee=int(plan_row.get("tuition_fee") or 0),
        plan_count=int(plan_row.get("plan_count") or 0),
        major_id=plan_row.get("major_id") or (major.id if major else None),
        discipline_code=plan_row.get("discipline_code") or (major.discipline_code if major else None),        subject_requirement=subject_requirement,
        requirements=tuple(build_requirement(row) for row in (requirements or ())),
        special_requirements_text=plan_row.get("special_requirements"),
        series_match_quality=plan_row.get("series_match_quality") or "EXACT",
        is_demo=bool(plan_row.get("is_demo")),
    )


def build_candidate_context(
    *,
    profile: Profile,
    preference: PreferenceProfile | None = None,
    subject_scores: list[SubjectScore] | None = None,
    attributes: list[Attribute] | None = None,
    current_ranked_candidates: int | None = None,
) -> CandidateContext:
    subjects = frozenset(str(code) for code in (profile.subjects or []))
    attr_map: dict[str, Any] = {}
    for attribute in attributes or ():
        if attribute.value_bool is not None:
            attr_map[attribute.attr_key] = attribute.value_bool
        elif attribute.value_int is not None:
            attr_map[attribute.attr_key] = attribute.value_int
        elif attribute.value_text is not None:
            attr_map[attribute.attr_key] = attribute.value_text

    return CandidateContext(
        rank=profile.rank,
        score=profile.score,
        current_ranked_candidates=current_ranked_candidates,
        subjects=subjects,
        # An empty subject set means "not provided", not "selected nothing"
        subjects_known=bool(subjects),
        subject_scores={row.subject_code: row.score for row in (subject_scores or ()) if row.score is not None},
        foreign_language=Fact.of(profile.foreign_language),
        sex=Fact.of(profile.sex),
        attributes=attr_map,
        hard_excludes=build_hard_excludes(preference),
    )


def build_hard_excludes(preference: PreferenceProfile | None) -> UserHardExcludes:
    if preference is None:
        return UserHardExcludes()

    major_ids = frozenset(
        row.major_id
        for row in preference.major_preferences
        if row.is_hard_exclude and row.major_id is not None
    )
    major_names = tuple(
        row.major_name
        for row in preference.major_preferences
        if row.is_hard_exclude and row.major_id is None and row.major_name
    )
    discipline_codes = frozenset(
        row.discipline_code
        for row in preference.major_preferences
        if row.is_hard_exclude and row.discipline_code
    )
    university_ids = frozenset(
        row.university_id
        for row in preference.university_preferences
        if row.is_hard_exclude and row.university_id is not None
    )
    province_codes = frozenset(
        row.province_code
        for row in preference.location_preferences
        if row.is_hard_exclude and row.province_code
    )
    city_codes = frozenset(
        row.city_code
        for row in preference.location_preferences
        if row.is_hard_exclude and row.city_code
    )

    return UserHardExcludes(
        major_ids=major_ids,
        major_names=major_names,
        discipline_codes=discipline_codes,
        university_ids=university_ids,
        province_codes=province_codes,
        city_codes=city_codes,
        exclude_cooperative=bool(preference.exclude_cooperative),
        exclude_private=bool(preference.exclude_private),
        max_tuition=preference.max_tuition,
    )


__all__ = [
    "build_candidate_context",
    "build_hard_excludes",
    "build_requirement",
    "build_subject_requirement",
    "build_target_plan",
]
