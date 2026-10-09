"""Profile endpoints: candidate profile, special attributes, preferences."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.candidate.schemas import (
    AttributeDTO,
    AttributeIn,
    PreferenceCreate,
    PreferenceDTO,
    ProfileCreate,
    ProfileDetail,
    ProfileDTO,
    ProfileUpdate,
)
from app.candidate.service import PreferenceService, ProfileService
from app.common.schemas import Page, PageParams, page_params

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.post("", response_model=ProfileDetail, status_code=status.HTTP_201_CREATED, summary="创建考生档案")
def create_profile(payload: ProfileCreate, session: Annotated[Session, Depends(get_session)]):
    service = ProfileService(session)
    detail = service.create(payload)
    session.commit()
    return detail


@router.get("", response_model=Page[ProfileDTO], summary="考生档案列表")
def list_profiles(
    session: Annotated[Session, Depends(get_session)],
    page: Annotated[PageParams, Depends(page_params)],
    cycle_id: Annotated[int | None, Query(description="按招生周期过滤")] = None,
):
    rows, total = ProfileService(session).list(
        limit=page.limit, offset=page.offset, cycle_id=cycle_id
    )
    return Page[ProfileDTO](
        items=[ProfileDTO.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{profile_id}", response_model=ProfileDetail, summary="考生档案详情")
def get_profile(profile_id: int, session: Annotated[Session, Depends(get_session)]):
    return ProfileService(session).get_detail(profile_id)


@router.patch("/{profile_id}", response_model=ProfileDetail, summary="修改考生档案（乐观锁）")
def update_profile(
    profile_id: int, payload: ProfileUpdate, session: Annotated[Session, Depends(get_session)]
):
    service = ProfileService(session)
    detail = service.update(profile_id, payload)
    session.commit()
    return detail


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除考生档案")
def delete_profile(profile_id: int, session: Annotated[Session, Depends(get_session)]):
    ProfileService(session).delete(profile_id)
    session.commit()


# ---------------- attributes ----------------


@router.get("/{profile_id}/attributes", response_model=list[AttributeDTO], summary="特殊条件列表")
def list_attributes(profile_id: int, session: Annotated[Session, Depends(get_session)]):
    return ProfileService(session).list_attributes(profile_id)


@router.put("/{profile_id}/attributes", response_model=AttributeDTO, summary="新增/更新特殊条件")
def upsert_attribute(
    profile_id: int, payload: AttributeIn, session: Annotated[Session, Depends(get_session)]
):
    row = ProfileService(session).set_attribute(profile_id, payload)
    session.commit()
    return AttributeDTO.model_validate(row)


@router.delete(
    "/{profile_id}/attributes/{attr_key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除特殊条件",
)
def delete_attribute(
    profile_id: int, attr_key: str, session: Annotated[Session, Depends(get_session)]
):
    ProfileService(session).delete_attribute(profile_id, attr_key)
    session.commit()


# ---------------- preferences ----------------


@router.post(
    "/{profile_id}/preferences",
    response_model=PreferenceDTO,
    status_code=status.HTTP_201_CREATED,
    summary="创建偏好档案（自动版本 +1，旧版本不再激活）",
)
def create_preference(
    profile_id: int, payload: PreferenceCreate, session: Annotated[Session, Depends(get_session)]
):
    dto = PreferenceService(session).create(profile_id, payload)
    session.commit()
    return dto


@router.get(
    "/{profile_id}/preferences", response_model=list[PreferenceDTO], summary="偏好档案全部版本"
)
def list_preferences(profile_id: int, session: Annotated[Session, Depends(get_session)]):
    return PreferenceService(session).list_versions(profile_id)


@router.get(
    "/{profile_id}/preferences/latest", response_model=PreferenceDTO, summary="最新偏好档案"
)
def latest_preference(profile_id: int, session: Annotated[Session, Depends(get_session)]):
    return PreferenceService(session).latest(profile_id)
