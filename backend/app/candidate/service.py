"""Candidate profile + preference services (Router → Service → Repository → DB)."""

from __future__ import annotations

from sqlalchemy.orm import Session, selectinload

from app.admission.models import AdmissionCycle
from app.admission.repository import CycleRepository
from app.candidate.models import PreferenceProfile, Profile
from app.candidate.repository import PreferenceRepository, ProfileRepository
from app.candidate.schemas import (
    AttributeIn,
    PreferenceCreate,
    PreferenceDTO,
    ProfileCreate,
    ProfileDetail,
    ProfileUpdate,
)
from app.common.errors import NotFoundError, RuleViolationError, VersionConflictError


class ProfileService:
    V1_EXAM_CATEGORY = "SUMMER_REGULAR"

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = ProfileRepository(session)
        self.cycles = CycleRepository(session)

    # ---------------- create / read ----------------

    def create(self, payload: ProfileCreate) -> ProfileDetail:
        cycle = self.cycles.get(payload.cycle_id)
        if cycle is None:
            raise NotFoundError(f"招生周期 {payload.cycle_id} 不存在")
        self._assert_v1_scope(cycle)

        profile = Profile(
            display_name=payload.display_name,
            cycle_id=cycle.id,
            exam_year=cycle.exam_year,
            province_code=cycle.province_code,
            exam_category=cycle.exam_category,
            score=payload.score,
            rank=payload.rank,
            rank_source="MANUAL",
            subjects=list(payload.subjects),
            foreign_language=payload.foreign_language,
            sex=payload.sex,
            version=1,
            notes=payload.notes,
        )
        self.repo.add(profile)

        if payload.subject_scores:
            self.repo.replace_subject_scores(
                profile.id, [row.model_dump() for row in payload.subject_scores]
            )
        for attribute in payload.attributes:
            self.repo.upsert_attribute(profile.id, attribute.model_dump())

        return self.get_detail(profile.id)

    def get_detail(self, profile_id: int) -> ProfileDetail:
        profile = self.session.get(
            Profile,
            profile_id,
            options=[selectinload(Profile.subject_scores), selectinload(Profile.attributes)],
        )
        if profile is None:
            raise NotFoundError(f"考生档案 {profile_id} 不存在")
        return ProfileDetail.model_validate(profile)

    def list(self, *, limit: int, offset: int, cycle_id: int | None = None) -> tuple[list[Profile], int]:
        rows = self.repo.list_recent(limit=limit, offset=offset, cycle_id=cycle_id)
        total = self.repo.count_recent(cycle_id=cycle_id)
        return rows, total

    def list_attributes(self, profile_id: int):
        detail = self.get_detail(profile_id)
        return detail.attributes

    # ---------------- update ----------------

    def update(self, profile_id: int, payload: ProfileUpdate) -> ProfileDetail:
        profile = self.repo.require(profile_id)
        if payload.expected_version is not None and payload.expected_version != profile.version:
            raise VersionConflictError(
                "考生档案已在其他位置被修改",
                details={"expected": payload.expected_version, "actual": profile.version},
            )

        data = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
        for key, value in data.items():
            setattr(profile, key, value)
        if profile.score is None and profile.rank is None:
            raise RuleViolationError("score 与 rank 至少需要保留一个")
        profile.version += 1
        self.session.flush()
        return self.get_detail(profile_id)

    def delete(self, profile_id: int) -> None:
        profile = self.repo.require(profile_id)
        self.session.delete(profile)
        self.session.flush()

    # ---------------- attributes ----------------

    def set_attribute(self, profile_id: int, payload: AttributeIn):
        self.repo.require(profile_id)
        return self.repo.upsert_attribute(profile_id, payload.model_dump())

    def delete_attribute(self, profile_id: int, attr_key: str) -> None:
        self.repo.require(profile_id)
        if not self.repo.delete_attribute(profile_id, attr_key):
            raise NotFoundError(f"属性 {attr_key} 不存在")

    def _assert_v1_scope(self, cycle: AdmissionCycle) -> None:
        if cycle.exam_category != self.V1_EXAM_CATEGORY:
            raise RuleViolationError(
                f"V1 仅支持 {self.V1_EXAM_CATEGORY}，收到 {cycle.exam_category}",
                details={"cycle_id": cycle.id},
            )
        if cycle.batch_code != "REGULAR_BATCH_1":
            raise RuleViolationError(
                f"V1 仅支持 REGULAR_BATCH_1，收到 {cycle.batch_code}", details={"cycle_id": cycle.id}
            )


class PreferenceService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = PreferenceRepository(session)
        self.profiles = ProfileRepository(session)
        self.cycles = CycleRepository(session)

    def create(self, profile_id: int, payload: PreferenceCreate) -> PreferenceDTO:
        profile = self.profiles.require(profile_id)
        cycle = self.cycles.require(profile.cycle_id)

        # Policy numbers come from the cycle, never from a constant in code.
        target_count = payload.target_count or cycle.application_limit
        if target_count > cycle.application_limit:
            raise RuleViolationError(
                f"志愿数量上限为 {cycle.application_limit}（来自 admission_cycles），"
                f"不能设置为 {target_count}",
                details={"application_limit": cycle.application_limit, "cycle_id": cycle.id},
            )

        version = self.repo.next_version(profile_id)
        # Preference versions are append-only: deactivate the previous one
        for previous in self.repo.versions_for_profile(profile_id):
            previous.is_active = False

        preference = PreferenceProfile(
            profile_id=profile_id,
            version=version,
            risk_mode=payload.risk_mode,
            candidate_rank=profile.rank,
            max_tuition=payload.max_tuition,
            min_tuition=payload.min_tuition,
            exclude_private=payload.exclude_private,
            exclude_cooperative=payload.exclude_cooperative,
            accept_unknown_requirements=payload.accept_unknown_requirements,
            target_count=target_count,
            min_safe_count=payload.min_safe_count,
            dimension_weights=payload.dimension_weights,
            notes=payload.notes,
            is_active=True,
        )
        self.repo.add(preference)
        self.repo.add_children(preference, payload.model_dump())
        self.session.flush()
        return self.get(preference.id)

    def get(self, preference_id: int) -> PreferenceDTO:
        preference = self.session.get(
            PreferenceProfile,
            preference_id,
            options=[
                selectinload(PreferenceProfile.major_preferences),
                selectinload(PreferenceProfile.location_preferences),
                selectinload(PreferenceProfile.university_preferences),
                selectinload(PreferenceProfile.other_preferences),
            ],
        )
        if preference is None:
            raise NotFoundError(f"偏好档案 {preference_id} 不存在")
        return PreferenceDTO.model_validate(preference)

    def latest(self, profile_id: int) -> PreferenceDTO:
        preference = self.repo.latest_for_profile(profile_id)
        if preference is None:
            raise NotFoundError(f"考生 {profile_id} 还没有偏好档案")
        return self.get(preference.id)

    def list_versions(self, profile_id: int) -> list[PreferenceDTO]:
        self.profiles.require(profile_id)
        return [self.get(row.id) for row in self.repo.versions_for_profile(profile_id)]
