"""Remove throwaway profiles created by the audit scripts, cascading correctly.

The audit runs create profiles (and therefore sessions, items, schemes and health checks)
that should not linger in the development database. FK order matters: sessions reference
preference profiles, so a naive delete violates the constraint.

Only profiles matching an explicit, narrow rule are removed, and every dependent row is
counted before deletion so nothing is removed silently.

    python -m app.ingestion.cleanup_audit_data --dry-run
    python -m app.ingestion.cleanup_audit_data --write
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

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
    HealthCheck,
    HealthCheckIssue,
    RecommendationItem,
    Scheme,
    SchemeItem,
    SchemeVersion,
)
from app.recommendation.models import (
    Session as RecommendationSession,
)

# Profiles the audits create: an out-of-range rank, or a plain 60,000 probe.
AUDIT_RANKS = (600000, 60000)


def _targets(session: Session) -> list[tuple[int, int, int]]:
    rows = session.execute(
        text(
            """
            select p.id, p.rank,
                   (select count(*) from recommendation.sessions s where s.profile_id = p.id)
            from candidate.profiles p
            where p.rank = any(:ranks)
            order by p.id
            """
        ),
        {"ranks": list(AUDIT_RANKS)},
    ).all()
    return [(int(r[0]), int(r[1]), int(r[2])) for r in rows]


def _purge(session: Session, profile_ids: list[int]) -> dict[str, int]:
    counts: dict[str, int] = {}
    pref_ids = list(
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
        list(session.execute(select(Scheme.id).where(Scheme.session_id.in_(session_ids))).scalars())
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
    if pref_ids:
        for model in (
            MajorPreference,
            LocationPreference,
            UniversityPreference,
            OtherPreference,
        ):
            session.execute(delete(model).where(model.preference_profile_id.in_(pref_ids)))
        counts["preference_versions"] = session.execute(
            delete(PreferenceProfile).where(PreferenceProfile.id.in_(pref_ids))
        ).rowcount
    session.execute(delete(SubjectScore).where(SubjectScore.profile_id.in_(profile_ids)))
    session.execute(delete(Attribute).where(Attribute.profile_id.in_(profile_ids)))
    counts["profiles"] = session.execute(
        delete(Profile).where(Profile.id.in_(profile_ids))
    ).rowcount
    session.flush()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="真正删除（默认只列出）")
    parser.add_argument(
        "--all-60000",
        action="store_true",
        help="同时删除所有位次 60000 的档案（含 E2E 建立的），默认只删无会话引用的",
    )
    args = parser.parse_args(argv)

    with session_scope() as session:
        targets = _targets(session)
        if not targets:
            print("没有匹配的档案。")
            return 0

        print("匹配到的档案（id / 位次 / 会话数）:")
        for profile_id, rank, sessions in targets:
            print(f"  {profile_id:>4}  {rank:>8}  会话 {sessions}")

        doomed = [
            profile_id
            for profile_id, rank, sessions in targets
            if rank == 600000 or args.all_60000 or sessions == 0
        ]
        print()
        print(f"将删除 {len(doomed)} 个: {doomed}")
        if not args.write:
            print("演练结束，未删除。加 --write 执行。")
            return 0

        counts = _purge(session, doomed)
        print("已删除:")
        for key, value in counts.items():
            print(f"  {key:<22} {value}")

    with session_scope() as session:
        print()
        print("剩余:")
        print(f"  档案 {session.execute(select(Profile.id)).all().__len__()}")
        print(
            f"  会话 {session.execute(select(RecommendationSession.id)).all().__len__()}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
