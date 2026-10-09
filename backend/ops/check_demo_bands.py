"""Dev probe: reconcile the intended demo bucket spread with what ACS actually yields."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.admission.models import AdmissionCycle
from app.candidate.models import PreferenceProfile, Profile
from app.recommendation.pipeline import RecommendationPipeline

engine = create_engine(os.environ["DATABASE_URL"])
with Session(engine) as session:
    cycle = session.execute(select(AdmissionCycle)).scalar_one()
    profile = session.execute(
        select(Profile).order_by(Profile.id.desc()).limit(1)
    ).scalar_one_or_none()
    if profile is None:
        profile = Profile(
            cycle_id=cycle.id, exam_year=cycle.exam_year, rank=60_000, score=590,
            subjects=["PHY", "CHM", "BIO"], foreign_language="ENG",
        )
        session.add(profile)
        session.flush()
    preference = PreferenceProfile(
        profile_id=profile.id, version=100, risk_mode="BALANCED",
        candidate_rank=60_000, target_count=96,
    )
    session.add(preference)
    session.flush()

    result = RecommendationPipeline(session).run(
        profile_id=profile.id, preference_id=preference.id
    )

    rows = []
    for bundle in result.bundles:
        if not bundle.acs:
            continue
        rows.append(
            (
                bundle.acs.acs,
                bundle.plan_row["university_name"][:22],
                bundle.plan_row["plan_name"],
                bundle.acs.weighted_mean_d,
                bundle.acs.confidence,
                bundle.target.province_code,
            )
        )
    rows.sort()

    print(f"{'ACS':>6} {'d':>8} {'conf':>5}  {'省份':<8} 计划")
    print("-" * 78)
    for acs, _uni, plan, d, conf, province in rows[:: max(1, len(rows) // 18)]:
        print(f"{acs:>6.1f} {d or 0:>8.3f} {conf:>5.2f}  {province:<8} {plan}")

    print()
    print("最小 ACS 5 条:")
    for acs, _uni, plan, d, conf, province in rows[:5]:
        print(f"  {acs:>6.1f}  d={(d or 0):+.3f}  conf={conf:.2f}  {province} {plan}")
    print("最大 ACS 5 条:")
    for acs, _uni, plan, d, conf, province in rows[-5:]:
        print(f"  {acs:>6.1f}  d={(d or 0):+.3f}  conf={conf:.2f}  {province} {plan}")

    session.rollback()
