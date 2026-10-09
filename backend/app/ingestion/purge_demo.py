"""Switch the database from DEMO fixtures to the real Shandong dataset.

Removes the DEMO snapshot and everything derived from it (profiles, preference
versions, sessions, scheme versions, health runs), drops the DEMO universities
(marked by the `DEMO` ministry_code prefix), and leaves the real 2022-2025 data as
the only PUBLISHED snapshot.

    python -m app.ingestion.purge_demo --dry-run
    python -m app.ingestion.purge_demo --write
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.admission.models import (
    AdmissionCycle,
    AdmissionOutcome,
    AdmissionPlan,
    DatasetSnapshot,
    ProgramSeries,
    ScoreRankSegment,
)
from app.candidate.models import PreferenceProfile, Profile
from app.db.session import session_scope
from app.reference.models import University

REAL_SNAPSHOT_CODE = "SD-2022-2025"


def _delete_snapshot_data(session: Session, snapshot_ids: list[int]) -> dict[str, int]:
    counts: dict[str, int] = {}
    from app.admission.models import AdmissionRequirement

    plan_ids = list(
        session.execute(
            select(AdmissionPlan.id).where(AdmissionPlan.snapshot_id.in_(snapshot_ids))
        ).scalars()
    )
    if plan_ids:
        counts["requirements"] = session.execute(
            delete(AdmissionRequirement).where(AdmissionRequirement.plan_id.in_(plan_ids))
        ).rowcount
        counts["plans"] = session.execute(
            delete(AdmissionPlan).where(AdmissionPlan.id.in_(plan_ids))
        ).rowcount
    counts["outcomes"] = session.execute(
        delete(AdmissionOutcome).where(AdmissionOutcome.snapshot_id.in_(snapshot_ids))
    ).rowcount
    session.flush()
    return counts


def _delete_candidate_data(session: Session, profile_ids: list[int]) -> dict[str, int]:
    """Remove everything hanging off the given profiles, leaf tables first."""
    from app.candidate.models import (
        LocationPreference,
        MajorPreference,
        OtherPreference,
        UniversityPreference,
    )
    from app.recommendation.models import (
        HealthCheck,
        HealthCheckIssue,
        RecommendationItem,
        Scheme,
        SchemeItem,
        SchemeVersion,
    )
    from app.recommendation.models import Session as RecommendationSession

    counts: dict[str, int] = {}
    preference_ids = list(
        session.execute(
            select(PreferenceProfile.id).where(PreferenceProfile.profile_id.in_(profile_ids))
        ).scalars()
    )
    session_ids = list(
        session.execute(
            select(RecommendationSession.id).where(
                RecommendationSession.profile_id.in_(profile_ids)
            )
        ).scalars()
    )
    scheme_ids = (
        list(
            session.execute(
                select(Scheme.id).where(Scheme.session_id.in_(session_ids))
            ).scalars()
        )
        if session_ids
        else []
    )
    version_ids = (
        list(
            session.execute(
                select(SchemeVersion.id).where(SchemeVersion.scheme_id.in_(scheme_ids))
            ).scalars()
        )
        if scheme_ids
        else []
    )
    health_ids = (
        list(
            session.execute(
                select(HealthCheck.id).where(HealthCheck.scheme_version_id.in_(version_ids))
            ).scalars()
        )
        if version_ids
        else []
    )

    if health_ids:
        counts["health_issues"] = session.execute(
            delete(HealthCheckIssue).where(HealthCheckIssue.health_check_id.in_(health_ids))
        ).rowcount
        session.execute(delete(HealthCheck).where(HealthCheck.id.in_(health_ids)))
    if version_ids:
        counts["scheme_items"] = session.execute(
            delete(SchemeItem).where(SchemeItem.scheme_version_id.in_(version_ids))
        ).rowcount
        session.execute(delete(SchemeVersion).where(SchemeVersion.id.in_(version_ids)))
    if scheme_ids:
        counts["schemes"] = session.execute(
            delete(Scheme).where(Scheme.id.in_(scheme_ids))
        ).rowcount
    if session_ids:
        counts["recommendation_items"] = session.execute(
            delete(RecommendationItem).where(RecommendationItem.session_id.in_(session_ids))
        ).rowcount
        counts["sessions"] = session.execute(
            delete(RecommendationSession).where(RecommendationSession.id.in_(session_ids))
        ).rowcount
    if preference_ids:
        for model in (MajorPreference, LocationPreference, UniversityPreference, OtherPreference):
            session.execute(delete(model).where(model.preference_profile_id.in_(preference_ids)))
        counts["preference_versions"] = session.execute(
            delete(PreferenceProfile).where(PreferenceProfile.id.in_(preference_ids))
        ).rowcount
    counts["profiles"] = session.execute(delete(Profile).where(Profile.id.in_(profile_ids))).rowcount
    session.flush()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Purge DEMO fixtures, keep real data.")
    parser.add_argument("--write", action="store_true", help="真正执行（默认只统计）")
    args = parser.parse_args(argv)

    with session_scope() as session:
        real = session.execute(
            select(DatasetSnapshot).where(DatasetSnapshot.code == REAL_SNAPSHOT_CODE)
        ).scalar_one_or_none()
        if real is None:
            print(f"找不到真实数据快照 {REAL_SNAPSHOT_CODE}，请先运行 etl_shandong --write")
            return 1
        real.status = "PUBLISHED"
        counts: dict[str, int] = {}

        demo_snapshots = list(
            session.execute(
                select(DatasetSnapshot).where(DatasetSnapshot.is_demo.is_(True))
            ).scalars()
        )
        demo_snapshot_ids = [row.id for row in demo_snapshots]
        print(f"真实快照: {real.code} (id={real.id})")
        print(f"DEMO 快照: {[(s.code, s.id) for s in demo_snapshots]}")
        print()

        # Profiles tied to the DEMO cycle
        demo_cycles = list(
            session.execute(
                select(AdmissionCycle.id).where(AdmissionCycle.policy_version.like("sd-2026-demo%"))
            ).scalars()
        )
        demo_profiles = list(
            session.execute(
                select(Profile.id).where(
                    or_(
                        Profile.cycle_id.in_(demo_cycles) if demo_cycles else False,
                        Profile.exam_year == 2026,
                    )
                )
            ).scalars()
        )
        print(f"DEMO 周期: {demo_cycles}   待删考生档案: {len(demo_profiles)}")

        if not args.write:
            print()
            print("演练结束。加 --write 执行清理。")
            return 0

        if demo_profiles:
            counts.update(_delete_candidate_data(session, demo_profiles))
        session.flush()

        counts = _delete_snapshot_data(session, demo_snapshot_ids) if demo_snapshot_ids else {}
        if demo_snapshot_ids:
            session.execute(delete(DatasetSnapshot).where(DatasetSnapshot.id.in_(demo_snapshot_ids)))

        # DEMO source documents, score-rank segments and universities
        from app.admission.models import SourceDocument

        counts["score_segments"] = session.execute(
            delete(ScoreRankSegment).where(ScoreRankSegment.source_document_id.in_(
                select(SourceDocument.id).where(SourceDocument.quality == "DEMO")
            ))
        ).rowcount
        counts["sources"] = session.execute(
            delete(SourceDocument).where(SourceDocument.quality == "DEMO")
        ).rowcount

        # Universities whose plans are gone and whose code carries the DEMO marker
        referenced = select(ProgramSeries.id).where(ProgramSeries.university_id == University.id)
        counts["universities"] = session.execute(
            delete(University).where(
                University.ministry_code.like("DEMO%"), ~referenced.exists()
            )
        ).rowcount
        counts["series_orphans"] = session.execute(
            delete(ProgramSeries).where(
                ~select(AdmissionPlan.id).where(AdmissionPlan.series_id == ProgramSeries.id).exists()
            )
        ).rowcount
        counts["cycles"] = session.execute(
            delete(AdmissionCycle).where(AdmissionCycle.policy_version.like("sd-2026-demo%"))
        ).rowcount
        session.flush()

        print()
        print("已清理：")
        for key, value in counts.items():
            print(f"  - {key}: {value}")

        remaining = {
            "真实快照": session.execute(
                select(func.count()).select_from(DatasetSnapshot)
            ).scalar_one(),
            "院校": session.execute(select(func.count()).select_from(University)).scalar_one(),
            "专业序列": session.execute(select(func.count()).select_from(ProgramSeries)).scalar_one(),
            "招生计划": session.execute(select(func.count()).select_from(AdmissionPlan)).scalar_one(),
            "历史录取": session.execute(select(func.count()).select_from(AdmissionOutcome)).scalar_one(),
            "一分一段": session.execute(select(func.count()).select_from(ScoreRankSegment)).scalar_one(),
        }
        print()
        print("剩余：")
        for key, value in remaining.items():
            print(f"  - {key}: {value:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
