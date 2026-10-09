"""Assemble scoring inputs from persisted rows (the ORM → pure-scoring bridge)."""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence

from app.admission.models import AdmissionOutcome
from app.candidate.models import PreferenceProfile
from app.scoring.acs import ACSService
from app.scoring.preference import (
    LocationSelector,
    MajorSelector,
    PreferenceInput,
    PreferenceService,
    PreferenceWeights,
    UniversitySelector,
)
from app.scoring.types import ACSEvaluation, HistoryPoint

# Provenance quality of a plan/outcome row -> source quality used by Confidence
DEMO_SOURCE_QUALITY = "DEMO"
DEFAULT_SOURCE_QUALITY = "OFFICIAL"


def history_from_outcomes(
    outcomes: Sequence[AdmissionOutcome],
    *,
    series_match_quality: str,
    ranked_candidates_by_year: Mapping[int, int],
    default_source_quality: str = DEFAULT_SOURCE_QUALITY,
    is_demo: bool = False,
) -> list[HistoryPoint]:
    """Turn `admission_outcomes` rows into ACS history points.

    Rows whose year has no published candidate population are kept in the list
    with `ranked_candidates=None`: the scorer drops them and says so, which is
    more honest than silently omitting them.
    """
    quality = DEMO_SOURCE_QUALITY if is_demo else default_source_quality
    points: list[HistoryPoint] = []
    for outcome in outcomes:
        points.append(
            HistoryPoint(
                exam_year=outcome.exam_year,
                min_rank=outcome.min_rank,
                ranked_candidates=ranked_candidates_by_year.get(outcome.exam_year),
                plan_count=outcome.plan_count,
                min_score=outcome.min_score,
                admitted_count=outcome.admitted_count,
                source_quality=quality,
                series_match_quality=series_match_quality or "EXACT",
            )
        )
    points.sort(key=lambda point: point.exam_year)
    return points


def evaluate_acs(
    *,
    service: ACSService,
    candidate_rank: int | None,
    current_ranked_candidates: int | None,
    history: Sequence[HistoryPoint],
    plan_current: int | None,
    series_match_quality: str,
    data_year: int | None = None,
) -> ACSEvaluation:
    return service.evaluate(
        candidate_rank=candidate_rank,
        current_ranked_candidates=current_ranked_candidates,
        history=history,
        plan_current=plan_current,
        series_match_quality=series_match_quality,
        data_year=data_year,
    )


# ---------------- preference assembly ----------------


def preference_weights_from(profile: PreferenceProfile | None) -> PreferenceWeights:
    """Read user preference rows into the pure scorer's weight structure."""
    if profile is None:
        return PreferenceWeights()

    def weight_of(row) -> float:
        return float(row.weight if row.weight is not None else 100.0)

    major_includes = tuple(
        MajorSelector(
            major_id=row.major_id,
            discipline_code=row.discipline_code,
            name=row.major_name,
            weight=weight_of(row),
        )
        for row in profile.major_preferences
        if row.polarity == "INCLUDE" and not row.is_hard_exclude
    )
    # Hard excludes are handled by the Rule Engine; non-hard EXCLUDE rows act as
    # strong preference penalties here.
    major_excludes = tuple(
        MajorSelector(
            major_id=row.major_id,
            discipline_code=row.discipline_code,
            name=row.major_name,
            weight=weight_of(row),
        )
        for row in profile.major_preferences
        if row.polarity == "EXCLUDE" and row.is_hard_exclude
    )
    location_includes = tuple(
        LocationSelector(
            province_code=row.province_code,
            city_code=row.city_code,
            weight=weight_of(row),
        )
        for row in profile.location_preferences
        if row.polarity == "INCLUDE" and not row.is_hard_exclude
    )
    location_excludes = tuple(
        LocationSelector(province_code=row.province_code, city_code=row.city_code, weight=weight_of(row))
        for row in profile.location_preferences
        if row.polarity == "EXCLUDE" and row.is_hard_exclude
    )
    university_includes = tuple(
        UniversitySelector(university_id=row.university_id, trait=row.trait, weight=weight_of(row))
        for row in profile.university_preferences
        if row.polarity == "INCLUDE" and not row.is_hard_exclude
    )
    university_excludes = tuple(
        UniversitySelector(university_id=row.university_id, trait=row.trait, weight=weight_of(row))
        for row in profile.university_preferences
        if row.polarity == "EXCLUDE" and row.is_hard_exclude
    )

    traits: dict[str, tuple[float, object]] = {}
    career_scores: list[float] = []
    academic_scores: list[float] = []
    for row in profile.other_preferences:
        if row.dimension == "CAREER" and row.value_num is not None:
            career_scores.append(float(row.value_num))
        elif row.dimension == "ACADEMIC" and row.value_num is not None:
            academic_scores.append(float(row.value_num))
        else:
            traits[f"{row.dimension}:{row.key}"] = (weight_of(row), row.value_text or row.value_num)

    weights = dict(profile.dimension_weights or {})
    return PreferenceWeights(
        weights=weights or PreferenceWeights().weights,
        major_includes=major_includes,
        major_excludes=major_excludes,
        location_includes=location_includes,
        location_excludes=location_excludes,
        university_includes=university_includes,
        university_excludes=university_excludes,
        trait_preferences=traits,
    )


def plan_academic_score(traits: Mapping[str, bool]) -> float | None:
    """Derive the 升学/学校特征 score from university traits (985/211/双一流/研究生院)."""
    keys = ("IS_985", "IS_211", "IS_DOUBLE_FIRST_CLASS", "HAS_POSTGRAD")
    known = [traits[key] for key in keys if key in traits]
    if not known:
        return None
    return 100.0 * (sum(1 for flag in known if flag) / len(known))


def evaluate_preference(
    *,
    service: PreferenceService,
    weights: PreferenceWeights,
    major_id: int | None,
    discipline_code: str | None,
    major_names: Sequence[str],
    university_id: int,
    university_name: str,
    university_traits: Mapping[str, bool],
    province_code: str | None,
    city_code: str | None,
    tuition_fee: int,
    career_score: float | None = None,
    personal_score: float | None = None,
    tuition_budget: int | None = None,
    tuition_floor: int | None = None,
    interest_keywords: Sequence[str] = (),
):
    major_text = " ".join(name for name in major_names if name)
    academic = plan_academic_score(university_traits)
    candidate = PreferenceInput(
        major_id=major_id,
        discipline_code=discipline_code,
        major_names=tuple(name for name in major_names if name),
        major_text=major_text,
        interest_keywords=tuple(interest_keywords),
        university_id=university_id,
        university_name=university_name,
        university_traits=university_traits,
        province_code=province_code,
        city_code=city_code,
        tuition_fee=tuition_fee,
        tuition_budget=tuition_budget,
        tuition_floor=tuition_floor,
        academic_score=academic,
        career_score=career_score,
        personal_score=personal_score,
    )
    return service.evaluate(candidate=candidate, weights=weights)


def median_plan_count(points: Sequence[HistoryPoint]) -> float | None:
    plans = [point.plan_count for point in points if point.plan_count and point.plan_count > 0]
    return float(statistics.median(plans)) if plans else None


__all__ = [
    "evaluate_acs",
    "evaluate_preference",
    "history_from_outcomes",
    "median_plan_count",
    "plan_academic_score",
    "preference_weights_from",
]
