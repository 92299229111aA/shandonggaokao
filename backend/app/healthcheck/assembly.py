"""Build a Health Check fact snapshot from a persisted scheme version.

This is the only place health checking touches persistence, and it is one-way:
ORM rows in, frozen dataclasses out. The check engine itself never sees a Session.
"""

from __future__ import annotations

import statistics
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admission.models import AdmissionCycle, AdmissionOutcome, DatasetSnapshot
from app.admission.repository import SnapshotRepository
from app.candidate.models import PreferenceProfile
from app.common.errors import NotFoundError
from app.healthcheck.types import (
    HealthCheckContext,
    PreferenceFact,
    SchemeFact,
    SnapshotFact,
)
from app.optimizer.types import DEFAULT_RISK_TARGETS
from app.recommendation.models import RecommendationItem, Scheme
from app.rules.mapping import build_hard_excludes
from app.schemes.service import SchemeService


def build_context(
    session: Session,
    *,
    scheme_id: int,
    version_no: int | None = None,
    recheck_eligibility: bool = False,
) -> HealthCheckContext:
    scheme = session.get(Scheme, scheme_id)
    if scheme is None:
        raise NotFoundError(f"志愿方案 {scheme_id} 不存在")

    service = SchemeService(session)
    dto = service.get(scheme_id) if version_no is None else service.get_version(scheme_id, version_no)

    run = scheme.session_id
    from app.recommendation.models import Session as RunSession

    run_row = session.get(RunSession, run)
    cycle = session.get(AdmissionCycle, run_row.cycle_id) if run_row else None

    preference_row = (
        session.get(PreferenceProfile, run_row.preference_profile_id) if run_row else None
    )
    preference = _preference_fact(preference_row, target_count=dto.target_count)

    snapshot_row = session.get(DatasetSnapshot, run_row.snapshot_id) if run_row else None
    latest = SnapshotRepository(session).latest_published()

    facts = _scheme_facts(session, dto.items)
    risk_targets = DEFAULT_RISK_TARGETS.get(dto.risk_mode, DEFAULT_RISK_TARGETS["BALANCED"])

    return HealthCheckContext(
        items=tuple(facts),
        application_limit=cycle.application_limit if cycle else 96,
        risk_targets=dict(risk_targets),
        preference=preference,
        snapshot=_snapshot_fact(snapshot_row),
        latest_published_snapshot=_snapshot_fact(latest),
        target_exam_year=cycle.exam_year if cycle else None,
        recheck_enabled=recheck_eligibility,
    )


def _snapshot_fact(row: DatasetSnapshot | None) -> SnapshotFact | None:
    if row is None:
        return None
    year = row.published_at.year if row.published_at else None
    if year is None and row.code:
        # DEMO-2026 style codes carry the data year when published_at is absent
        for token in row.code.replace("_", "-").split("-"):
            if token.isdigit() and len(token) == 4:
                year = int(token)
                break
    return SnapshotFact(
        id=row.id,
        code=row.code,
        status=row.status,
        published_year=year,
        is_demo=row.is_demo,
    )


def _preference_fact(row: PreferenceProfile | None, *, target_count: int) -> PreferenceFact:
    if row is None:
        return PreferenceFact(target_count=target_count)
    hard = build_hard_excludes(row)
    return PreferenceFact(
        risk_mode=row.risk_mode,
        max_tuition=row.max_tuition,
        min_tuition=row.min_tuition,
        exclude_private=row.exclude_private,
        exclude_cooperative=row.exclude_cooperative,
        min_safe_count=row.min_safe_count,
        target_count=target_count,
        hard_exclude_major_ids=hard.major_ids,
        hard_exclude_major_names=hard.major_names,
        hard_exclude_discipline_codes=hard.discipline_codes,
        hard_exclude_university_ids=hard.university_ids,
        hard_exclude_province_codes=hard.province_codes,
        hard_exclude_city_codes=hard.city_codes,
    )


def _scheme_facts(session: Session, items: list[Any]) -> list[SchemeFact]:
    """Enrich each scheme item with current-cycle plan facts and history."""
    if not items:
        return []

    item_ids = [item.recommendation_item_id for item in items]
    recommendation_rows = {
        row.id: row
        for row in session.execute(
            select(RecommendationItem).where(RecommendationItem.id.in_(item_ids))
        ).scalars()
    }
    plan_ids = [row.plan_id for row in recommendation_rows.values()]
    plan_rows = _plan_rows(session, plan_ids)
    history = _history_rows(session, [row["series_id"] for row in plan_rows.values()])

    facts: list[SchemeFact] = []
    for item in items:
        recommendation = recommendation_rows.get(item.recommendation_item_id)
        if recommendation is None:
            continue
        plan = plan_rows.get(recommendation.plan_id, {})
        series_id = plan.get("series_id")
        outcomes = history.get(int(series_id), []) if series_id is not None else []
        facts.append(
            SchemeFact(
                position=item.position,
                plan_id=recommendation.plan_id,
                program_label=item.program_label,
                university_id=int(plan.get("university_id") or 0),
                major_id=plan.get("major_id"),
                discipline_code=plan.get("discipline_code"),
                plan_name=plan.get("plan_name") or item.plan_name,
                university_name=plan.get("university_name") or item.university_name,
                eligibility=recommendation.eligibility,
                acs=recommendation.acs,
                confidence=recommendation.confidence,
                preference_score=recommendation.preference_score,
                desire_band=item.desire_band,
                risk_bucket=recommendation.risk_bucket,
                locked=item.locked,
                fixed_position=item.fixed_position,
                plan_count=int(plan.get("plan_count") or 0),
                tuition_fee=int(plan.get("tuition_fee") or 0),
                subject_requirement_text=plan.get("subject_rule_raw_text"),
                special_requirements_text=plan.get("special_requirements"),
                has_unknown_requirement=_has_unknown_requirement(recommendation),
                plan_count_history=tuple(
                    int(outcome["plan_count"]) for outcome in outcomes if outcome["plan_count"]
                ),
                min_rank_history=tuple(
                    int(outcome["min_rank"]) for outcome in outcomes if outcome["min_rank"]
                ),
                cooperative=plan.get("cooperative") or "NONE",
            )
        )
    return facts


def _has_unknown_requirement(recommendation: RecommendationItem) -> bool:
    """True when the plan carries a requirement that could not be auto-verified."""
    if recommendation.eligibility == "UNKNOWN":
        return True
    reasons = recommendation.eligibility_reasons or []
    return any(
        isinstance(reason, dict)
        and str(reason.get("code")) == "SPECIAL_REQUIREMENT_UNVERIFIED"
        for reason in reasons
    )


def _plan_rows(session: Session, plan_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not plan_ids:
        return {}
    from app.admission.repository import PlanRepository

    repo = PlanRepository(session)
    rows = [repo.get_candidate(plan_id) for plan_id in set(plan_ids)]
    return {int(row["plan_id"]): row for row in rows if row is not None}


def _history_rows(session: Session, series_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not series_ids:
        return {}
    rows = session.execute(
        select(AdmissionOutcome)
        .where(AdmissionOutcome.series_id.in_(set(series_ids)))
        .order_by(AdmissionOutcome.series_id, AdmissionOutcome.exam_year)
    ).scalars()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.series_id, []).append(
            {
                "exam_year": row.exam_year,
                "plan_count": row.plan_count,
                "min_rank": row.min_rank,
                "min_score": row.min_score,
            }
        )
    return grouped

def median_plan_history(fact: SchemeFact) -> float | None:
    counts = [count for count in fact.plan_count_history if count > 0]
    return float(statistics.median(counts)) if counts else None


__all__ = ["build_context", "median_plan_history"]
