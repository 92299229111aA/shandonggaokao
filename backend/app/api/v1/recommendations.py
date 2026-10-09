"""Recommendation endpoints: run the pipeline and read the scored candidate pool."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.common.enums import EligibilityStatus, RiskBucket
from app.common.schemas import PageParams, page_params
from app.recommendation.schemas import (
    RecommendationCreate,
    RecommendationItemDTO,
    RecommendationItemPage,
    RecommendationRunResponse,
    SessionDTO,
)
from app.recommendation.service import RecommendationService

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.post(
    "",
    response_model=RecommendationRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="生成推荐（锁定快照与全部版本）",
)
def create_recommendation(
    payload: RecommendationCreate, session: Annotated[Session, Depends(get_session)]
):
    """Runs the pipeline and persists the scored candidate pool.

    Locks `profile_version / preference_version / dataset_snapshot_id /
    policy_version / model_version` on the session so the run can be replayed.
    """
    return RecommendationService(session).run(
        profile_id=payload.profile_id,
        preference_id=payload.preference_id,
        cycle_id=payload.cycle_id,
        snapshot_id=payload.snapshot_id,
        locked_plan_ids=payload.locked_plan_ids,
    )


@router.get("/items/{item_id}", response_model=RecommendationItemDTO, summary="单个候选志愿详情")
def get_recommendation_item(item_id: int, session: Annotated[Session, Depends(get_session)]):
    """Declared before the `/{session_id}` routes so the literal path wins."""
    return RecommendationService(session).get_item(item_id)


@router.get("/{session_id}", response_model=SessionDTO, summary="推荐会话详情（含版本信息）")
def get_recommendation_session(session_id: int, session: Annotated[Session, Depends(get_session)]):
    return RecommendationService(session).get_session(session_id)


@router.get(
    "/{session_id}/items",
    response_model=RecommendationItemPage,
    summary="候选志愿列表（推荐卡片数据）",
)
def list_recommendation_items(
    session_id: int,
    session: Annotated[Session, Depends(get_session)],
    page: Annotated[PageParams, Depends(page_params)],
    risk_bucket: Annotated[RiskBucket | None, Query(description="按风险层过滤")] = None,
    eligibility: Annotated[EligibilityStatus | None, Query(description="按资格结论过滤")] = None,
    only_pooled: Annotated[bool, Query(description="只看进入候选池的志愿")] = True,
    search: Annotated[str | None, Query(description="学校或专业名搜索")] = None,
):
    return RecommendationService(session).list_items(
        session_id=session_id,
        limit=page.limit,
        offset=page.offset,
        risk_bucket=risk_bucket.value if risk_bucket else None,
        eligibility=eligibility.value if eligibility else None,
        only_pooled=only_pooled,
        search=search,
    )
