"""Catalog endpoints: universities, majors, plans, score→rank conversion."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.admission.catalog_service import CatalogService
from app.admission.schemas import (
    MajorDTO,
    PlanDetail,
    PlanDTO,
    ScoreRankRequest,
    ScoreRankResult,
    UniversityDTO,
)
from app.api.deps import get_session
from app.candidate.score_rank import ScoreRankService
from app.common.schemas import Page, PageParams, page_params

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/universities", response_model=Page[UniversityDTO], summary="院校列表")
def list_universities(
    session: Annotated[Session, Depends(get_session)],
    page: Annotated[PageParams, Depends(page_params)],
    search: Annotated[str | None, Query(description="校名模糊搜索")] = None,
    province_code: Annotated[str | None, Query(description="省份代码，如 37")] = None,
    is_985: Annotated[bool | None, Query(description="仅 985")] = None,
):
    items, total = CatalogService(session).list_universities(
        limit=page.limit, offset=page.offset, search=search, province_code=province_code, is_985=is_985
    )
    return Page[UniversityDTO](items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/majors", response_model=Page[MajorDTO], summary="专业（专业类）字典")
def list_majors(
    session: Annotated[Session, Depends(get_session)],
    page: Annotated[PageParams, Depends(page_params)],
    search: Annotated[str | None, Query(description="专业名或代码搜索")] = None,
    discipline_code: Annotated[str | None, Query(description="学科门类代码，如 08")] = None,
    level: Annotated[str | None, Query(description="MAJOR_CLASS 或 MAJOR")] = None,
):
    items, total = CatalogService(session).list_majors(
        limit=page.limit,
        offset=page.offset,
        search=search,
        discipline_code=discipline_code,
        level=level,
    )
    return Page[MajorDTO](items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/plans", response_model=Page[PlanDTO], summary="招生计划（志愿单位）列表")
def list_plans(
    session: Annotated[Session, Depends(get_session)],
    page: Annotated[PageParams, Depends(page_params)],
    cycle_id: Annotated[int, Query(description="招生周期 id")],
    snapshot_id: Annotated[int | None, Query(description="数据快照 id，缺省用最新已发布快照")] = None,
    search: Annotated[str | None, Query(description="专业名或校名搜索")] = None,
    university_id: Annotated[int | None, Query()] = None,
    province_code: Annotated[str | None, Query()] = None,
    city_code: Annotated[str | None, Query()] = None,
    max_tuition: Annotated[int | None, Query(ge=0, description="学费上限（元/年）")] = None,
):
    items, total = CatalogService(session).list_plans(
        cycle_id=cycle_id,
        snapshot_id=snapshot_id,
        limit=page.limit,
        offset=page.offset,
        search=search,
        university_id=university_id,
        province_code=province_code,
        city_code=city_code,
        max_tuition=max_tuition,
    )
    return Page[PlanDTO](items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/plans/{plan_id}", response_model=PlanDetail, summary="招生计划详情（含特殊要求）")
def get_plan(plan_id: int, session: Annotated[Session, Depends(get_session)]):
    return CatalogService(session).get_plan(plan_id)


@router.post(
    "/score-to-rank",
    response_model=ScoreRankResult,
    summary="由分数换算位次（一分一段表）",
)
def score_to_rank(payload: ScoreRankRequest, session: Annotated[Session, Depends(get_session)]):
    return ScoreRankService(session).convert(cycle_id=payload.cycle_id, score=payload.score)
