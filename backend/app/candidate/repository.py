"""Data access for the candidate schema."""

from __future__ import annotations

from sqlalchemy import desc, select

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
from app.common.repository import Repository


class ProfileRepository(Repository[Profile]):
    model = Profile

    def list_recent(self, *, limit: int, offset: int, cycle_id: int | None = None) -> list[Profile]:
        stmt = select(Profile).order_by(desc(Profile.created_at))
        if cycle_id is not None:
            stmt = stmt.where(Profile.cycle_id == cycle_id)
        return self.list_page(stmt, limit, offset)

    def count_recent(self, *, cycle_id: int | None = None) -> int:
        where = () if cycle_id is None else (Profile.cycle_id == cycle_id,)
        return self.count(*where)

    def get_detail(self, profile_id: int) -> Profile:
        return self.require(profile_id)

    def replace_subject_scores(self, profile_id: int, rows: list[dict]) -> list[SubjectScore]:
        existing = {
            row.subject_code: row
            for row in self.session.execute(
                select(SubjectScore).where(SubjectScore.profile_id == profile_id)
            ).scalars()
        }
        for row in rows:
            current = existing.get(row["subject_code"])
            if current is None:
                self.session.add(SubjectScore(profile_id=profile_id, **row))
            else:
                current.score = row.get("score")
                current.grade = row.get("grade")
        self.session.flush()
        return list(
            self.session.execute(
                select(SubjectScore).where(SubjectScore.profile_id == profile_id)
            ).scalars()
        )

    def upsert_attribute(self, profile_id: int, data: dict) -> Attribute:
        row = self.session.execute(
            select(Attribute).where(
                Attribute.profile_id == profile_id, Attribute.attr_key == data["attr_key"]
            )
        ).scalar_one_or_none()
        if row is None:
            row = Attribute(profile_id=profile_id, **data)
            self.session.add(row)
        else:
            for key, value in data.items():
                setattr(row, key, value)
        self.session.flush()
        return row

    def delete_attribute(self, profile_id: int, attr_key: str) -> bool:
        row = self.session.execute(
            select(Attribute).where(
                Attribute.profile_id == profile_id, Attribute.attr_key == attr_key
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True


class PreferenceRepository(Repository[PreferenceProfile]):
    model = PreferenceProfile

    def versions_for_profile(self, profile_id: int) -> list[PreferenceProfile]:
        return list(
            self.session.execute(
                select(PreferenceProfile)
                .where(PreferenceProfile.profile_id == profile_id)
                .order_by(desc(PreferenceProfile.version))
            ).scalars()
        )

    def latest_for_profile(self, profile_id: int) -> PreferenceProfile | None:
        return self.session.execute(
            select(PreferenceProfile)
            .where(PreferenceProfile.profile_id == profile_id)
            .order_by(desc(PreferenceProfile.version))
            .limit(1)
        ).scalar_one_or_none()

    def next_version(self, profile_id: int) -> int:
        latest = self.latest_for_profile(profile_id)
        return 1 if latest is None else latest.version + 1

    def add_children(self, preference: PreferenceProfile, payload: dict) -> None:
        for item in payload.get("major_preferences", []):
            self.session.add(MajorPreference(preference_profile_id=preference.id, **item))
        for item in payload.get("location_preferences", []):
            self.session.add(LocationPreference(preference_profile_id=preference.id, **item))
        for item in payload.get("university_preferences", []):
            self.session.add(UniversityPreference(preference_profile_id=preference.id, **item))
        for item in payload.get("other_preferences", []):
            self.session.add(OtherPreference(preference_profile_id=preference.id, **item))
        self.session.flush()
