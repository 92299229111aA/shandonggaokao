"""Recommendation endpoints: run the pipeline and read the scored candidate pool."""

from __future__ import annotations

from typing import Annotated, Literal

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
    UniversityGroupPage,
)
from app.recommendation.service import RecommendationService
from app.recommendation.sorts import ItemSort

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
    sort: Annotated[ItemSort, Query(description="候选浏览排序（不影响最终方案顺序）")] = (
        ItemSort.PREFERENCE
    ),
    province_name: Annotated[str | None, Query(description="按院校所在省过滤")] = None,
    is_985: Annotated[bool | None, Query(description="只看 985")] = None,
    is_211: Annotated[bool | None, Query(description="只看 211")] = None,
    is_double_first_class: Annotated[bool | None, Query(description="只看双一流")] = None,
    university_id: Annotated[
        int | None, Query(description="只看某一所院校（按学校聚合视图展开用）")
    ] = None,
    exclude_scheme_id: Annotated[
        int | None, Query(description="排除该方案当前版本已有的志愿（编辑器候选池用）")
    ] = None,
):
    return RecommendationService(session).list_items(
        session_id=session_id,
        limit=page.limit,
        offset=page.offset,
        risk_bucket=risk_bucket.value if risk_bucket else None,
        eligibility=eligibility.value if eligibility else None,
        only_pooled=only_pooled,
        search=search,
        sort=sort.value,
        province_name=province_name,
        is_985=is_985,
        is_211=is_211,
        is_double_first_class=is_double_first_class,
        university_id=university_id,
        exclude_scheme_id=exclude_scheme_id,
    )


@router.get(
    "/{session_id}/university-groups",
    response_model=UniversityGroupPage,
    summary="候选志愿按院校聚合（编辑器的“按学校”视图）",
)
def list_recommendation_university_groups(
    session_id: int,
    session: Annotated[Session, Depends(get_session)],
    page: Annotated[PageParams, Depends(page_params)],
    risk_bucket: Annotated[RiskBucket | None, Query(description="按风险层过滤")] = None,
    eligibility: Annotated[EligibilityStatus | None, Query(description="按资格结论过滤")] = None,
    only_pooled: Annotated[bool, Query(description="只看进入候选池的志愿")] = True,
    search: Annotated[str | None, Query(description="学校或专业名搜索")] = None,
    sort: Annotated[
        Literal["ITEM_COUNT_DESC", "ACS_DESC", "NAME"],
        Query(description="院校排序"),
    ] = "ITEM_COUNT_DESC",
    province_name: Annotated[str | None, Query(description="按院校所在省过滤")] = None,
    is_985: Annotated[bool | None, Query(description="只看 985")] = None,
    is_211: Annotated[bool | None, Query(description="只看 211")] = None,
    is_double_first_class: Annotated[bool | None, Query(description="只看双一流")] = None,
    exclude_scheme_id: Annotated[
        int | None, Query(description="排除该方案当前版本已有的志愿")
    ] = None,
):
    return RecommendationService(session).list_university_groups(
        session_id=session_id,
        limit=page.limit,
        offset=page.offset,
        risk_bucket=risk_bucket.value if risk_bucket else None,
        eligibility=eligibility.value if eligibility else None,
        only_pooled=only_pooled,
        search=search,
        sort=sort,
        province_name=province_name,
        is_985=is_985,
        is_211=is_211,
        is_double_first_class=is_double_first_class,
        exclude_scheme_id=exclude_scheme_id,
    )
