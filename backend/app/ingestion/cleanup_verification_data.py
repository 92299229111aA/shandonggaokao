"""Remove throwaway verification profiles, keeping the health-check baseline ones.

The browser/API verification runs each create a profile -> preference -> session -> scheme
chain. After enough runs that is 34 profiles and 657k recommendation_items of noise in the
development database.

Kept: profiles that own a scheme, which are the ones `scripts/sweep_healthcheck.py` and
`scripts/compare_health_scores.py` measure (they need a real scheme id to run).
Removed: everything else at the throwaway ranks.

    python -m app.ingestion.cleanup_verification_data            # 演练
    python -m app.ingestion.cleanup_verification_data --write
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import delete, select, text

from app.candidate.models import (
    Attribute,
    LocationPreference,
    MajorPreference,
    OtherPreference,
    PreferenceProfile,
    Profile,
    SubjectScore,
    UniversityPreference,
)
from app.db.session import session_scope
from app.recommendation.models import (
    RecommendationItem,
)
from app.recommendation.models import (
    Session as RecommendationSession,
)

THROWAWAY_RANKS = (60000, 600000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="真正删除（默认只列出）")
    args = parser.parse_args(argv)

    with session_scope() as session:
        rows = session.execute(
            text(
                """
                select p.id, p.rank,
                       (select count(*) from recommendation.schemes sc
                          join recommendation.sessions s on s.id = sc.session_id
                         where s.profile_id = p.id) as schemes
                from candidate.profiles p
                where p.rank = any(:ranks)
                order by p.id
                """
            ),
            {"ranks": list(THROWAWAY_RANKS)},
        ).all()

        keep = [int(r[0]) for r in rows if r[2] > 0]
        doomed = [int(r[0]) for r in rows if r[2] == 0]
        print(f"命中 {len(rows)} 个测试档案（位次 {THROWAWAY_RANKS}）")
        print(f"  保留（拥有方案，体检基线依赖）: {keep}")
        print(f"  删除（无方案）: {len(doomed)} 个: {doomed}")

        if not args.write:
            print()
            print("演练结束，未删除。加 --write 执行。")
            return 0

        if not doomed:
            print()
            print("没有需要删除的档案。")
            return 0

        session_ids = list(
            session.execute(
                select(RecommendationSession.id).where(
                    RecommendationSession.profile_id.in_(doomed)
                )
            ).scalars()
        )
        pref_ids = list(
            session.execute(
                select(PreferenceProfile.id).where(PreferenceProfile.profile_id.in_(doomed))
            ).scalars()
        )

        removed: dict[str, int] = {}
        if session_ids:
            removed["recommendation_items"] = session.execute(
                delete(RecommendationItem).where(RecommendationItem.session_id.in_(session_ids))
            ).rowcount
            removed["sessions"] = session.execute(
                delete(RecommendationSession).where(RecommendationSession.id.in_(session_ids))
            ).rowcount
        if pref_ids:
            for model in (
                MajorPreference,
                LocationPreference,
                UniversityPreference,
                OtherPreference,
            ):
                session.execute(delete(model).where(model.preference_profile_id.in_(pref_ids)))
            removed["preference_versions"] = session.execute(
                delete(PreferenceProfile).where(PreferenceProfile.id.in_(pref_ids))
            ).rowcount
        session.execute(delete(SubjectScore).where(SubjectScore.profile_id.in_(doomed)))
        session.execute(delete(Attribute).where(Attribute.profile_id.in_(doomed)))
        removed["profiles"] = session.execute(
            delete(Profile).where(Profile.id.in_(doomed))
        ).rowcount

        print()
        print("已删除:")
        for key, value in removed.items():
            print(f"  {key:<22} {value:,}")

    with session_scope() as session:
        print()
        print("剩余:")
        for label, sql in (
            ("档案", "select count(*) from candidate.profiles"),
            ("会话", "select count(*) from recommendation.sessions"),
            (
                "推荐条目",
                "select count(*) from recommendation.recommendation_items",
            ),
            ("方案", "select count(*) from recommendation.schemes"),
            ("体检报告", "select count(*) from recommendation.health_checks"),
        ):
            print(f"  {label:<12} {session.execute(text(sql)).scalar_one():>9,}")

        schemes = session.execute(
            text(
                "select sc.id, sc.current_version_no, sc.item_count_expr from ("
                "  select sc.id, sc.current_version_no,"
                "         (select item_count from recommendation.scheme_versions sv"
                "           where sv.scheme_id = sc.id and sv.version_no = sc.current_version_no) "
                "         as item_count_expr"
                "  from recommendation.schemes sc) sc order by sc.id"
            )
        ).all()
        print()
        print("保留的方案（可作体检基线）:")
        for row in schemes:
            print(f"  方案 {row[0]}  v{row[1]}  {row[2]} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
