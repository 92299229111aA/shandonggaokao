"""Run the real-data pipeline once and dump a compact snapshot for analysis.

Running the pipeline takes minutes and peaks in memory, so analysis is split out:
this writes docs/real-run.json, and scripts/analyse_real_run.py reads it.

    python -m app.ingestion.snapshot_run
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.admission.models import (
    AdmissionCycle,
    AdmissionOutcome,
    AdmissionPlan,
    ExamYearStats,
    ProgramSeries,
)
from app.candidate.models import PreferenceProfile, Profile
from app.common.enums import risk_bucket_for as risk_bucket
from app.db.session import session_scope
from app.recommendation.pipeline import RecommendationPipeline
from app.reference.models import University

OUT = Path(__file__).resolve().parents[3] / "docs" / "real-run.json"


def main() -> int:
    with session_scope() as session:
        cycle = session.execute(
            select(AdmissionCycle).where(AdmissionCycle.exam_year == 2025)
        ).scalars().first()
        if cycle is None:
            print("找不到 2025 招生周期")
            return 1

        rank = int(sys.argv[1]) if len(sys.argv) > 1 else 60_000
        subjects = sys.argv[2].split(",") if len(sys.argv) > 2 else ["PHY", "CHM", "BIO"]

        profile = Profile(
            cycle_id=cycle.id, exam_year=2025, rank=rank, score=590,
            subjects=subjects, foreign_language="ENG",
        )
        session.add(profile)
        session.flush()
        preference = PreferenceProfile(
            profile_id=profile.id, version=1, risk_mode="BALANCED",
            candidate_rank=rank, target_count=96,
        )
        session.add(preference)
        session.flush()

        result = RecommendationPipeline(session).run(
            profile_id=profile.id, preference_id=preference.id
        )
        stats = result.stats

        pooled = [b for b in result.bundles if b.poolable]
        items = []
        for bundle in pooled:
            acs = bundle.acs
            items.append(
                {
                    "uni": str(bundle.plan_row.get("university_name") or "")[:40],
                    "plan": str(bundle.plan_row.get("plan_name") or "")[:60],
                    "province": bundle.target.province_code,
                    "eligibility": bundle.eligibility.status.value,
                    "acs": round(acs.acs, 2) if acs else None,
                    "confidence": round(acs.confidence, 3) if acs else None,
                    "history_points": acs.history_points if acs else 0,
                    "d": round(acs.weighted_mean_d, 4) if acs and acs.weighted_mean_d is not None else None,
                    "years": list(acs.years_used) if acs else [],
                    "bucket": risk_bucket(acs.acs).value if acs else "NO_ACS",
                }
            )

        payload: dict[str, Any] = {
            "cycle": {"exam_year": cycle.exam_year, "limit": cycle.application_limit},
            "candidate": {"rank": rank, "subjects": subjects, "population": None},
            "dataset": {
                "universities": session.execute(
                    select(func.count()).select_from(University)
                ).scalar_one(),
                "series": session.execute(
                    select(func.count()).select_from(ProgramSeries)
                ).scalar_one(),
                "plans": session.execute(
                    select(func.count()).select_from(AdmissionPlan)
                ).scalar_one(),
                "outcomes": session.execute(
                    select(func.count()).select_from(AdmissionOutcome)
                ).scalar_one(),
                "provinces": session.execute(
                    select(func.count(func.distinct(University.province_code)))
                ).scalar_one(),
            },
            "stats": {
                field: getattr(stats, field)
                for field in (
                    "plans_scanned", "eligibility_pass", "eligibility_fail",
                    "eligibility_unknown", "hard_excluded", "pooled", "scored",
                )
                if hasattr(stats, field)
            },
            "buckets": dict(Counter(item["bucket"] for item in items)),
            "items": items,
        }
        population_row = session.execute(
            select(ExamYearStats.ranked_candidates).where(
                ExamYearStats.exam_year == 2025,
                ExamYearStats.program_level == "UNDERGRAD",
            )
        ).scalar_one_or_none()
        payload["candidate"]["population"] = population_row

        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"written {OUT}  ({OUT.stat().st_size / 1024:.0f} KB)")
        print(f"pooled={len(items)}  buckets={payload['buckets']}")
        session.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main())
