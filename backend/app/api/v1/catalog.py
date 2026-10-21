"""Catalog endpoints: universities, majors, plans, score→rank conversion."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admission.catalog_service import CatalogService
from app.admission.models import AdmissionCycle, AdmissionPlan, DatasetSnapshot, ProgramSeries
from app.admission.repository import CycleRepository, SnapshotRepository
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
from app.reference.models import University

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


# The 14 门类 of 《普通高等学校本科专业目录》. 军事学 (11) and 交叉学科 (14) are included;
# the previously hardcoded frontend list stopped at 艺术学 (13) and omitted both.
DISCIPLINES: tuple[tuple[str, str], ...] = (
    ("01", "哲学"),
    ("02", "经济学"),
    ("03", "法学"),
    ("04", "教育学"),
    ("05", "文学"),
    ("06", "历史学"),
    ("07", "理学"),
    ("08", "工学"),
    ("09", "农学"),
    ("10", "医学"),
    ("11", "军事学"),
    ("12", "管理学"),
    ("13", "艺术学"),
    ("14", "交叉学科"),
)


def _catalog_provinces(
    session: Session, cycle_id: int | None, snapshot_id: int | None
) -> list[dict[str, Any]]:
    """Distinct provinces that actually have plans in this snapshot, with counts."""
    stmt = (
        select(
            University.province_code,
            University.province_name,
            func.count(func.distinct(University.id)),
        )
        .select_from(University)
        .join(ProgramSeries, ProgramSeries.university_id == University.id)
        .join(AdmissionPlan, AdmissionPlan.series_id == ProgramSeries.id)
        .group_by(University.province_code, University.province_name)
    )
    if cycle_id is not None:
        stmt = stmt.where(AdmissionPlan.cycle_id == cycle_id)
    if snapshot_id is not None:
        stmt = stmt.where(AdmissionPlan.snapshot_id == snapshot_id)

    rows = [
        {"code": code, "name": name, "university_count": int(count), "is_home": code == "37"}
        for code, name, count in session.execute(stmt).all()
    ]
    # Shandong first (home province), then by how much choice the province actually offers.
    rows.sort(key=lambda item: (not item["is_home"], -item["university_count"]))
    return rows


@router.get("/preference-options", summary="偏好表单选项（取自当前数据）")
def preference_options(
    session: Annotated[Session, Depends(get_session)],
    cycle_id: Annotated[int | None, Query(description="招生周期；默认取当前激活周期")] = None,
) -> dict[str, Any]:
    """Options for the preference form, derived from the loaded data.

    The form used to hardcode 9 provinces although the data holds 31 provincial-level
    regions, which silently capped what a candidate could express. Returning the real
    list keeps the form from drifting from the data again.
    """
    cycle: AdmissionCycle | None = session.get(AdmissionCycle, cycle_id) if cycle_id else None
    if cycle is None:
        active = CycleRepository(session).active_cycles()
        cycle = active[0] if active else None
    snapshot: DatasetSnapshot | None = SnapshotRepository(session).latest_published()

    provinces = _catalog_provinces(
        session, cycle.id if cycle else None, snapshot.id if snapshot else None
    )

    # 学科 is not derivable: the plan files carry a per-school major sequence number and
    # no 门类 column, so nothing maps a plan to 工学/医学/… . Report it instead of
    # offering a control that matches nothing.
    mapped_plans = int(
        session.execute(
            select(func.count())
            .select_from(AdmissionPlan)
            .join(ProgramSeries, ProgramSeries.id == AdmissionPlan.series_id)
            .where(ProgramSeries.major_id.is_not(None))
        ).scalar_one()
    )
    disciplines_available = mapped_plans > 0

    return {
        "cycle_id": cycle.id if cycle else None,
        "snapshot_code": snapshot.code if snapshot else None,
        "provinces": provinces,
        "disciplines": [
            {"code": code, "name": name, "available": disciplines_available}
            for code, name in DISCIPLINES
        ],
        "notes": {
            "provinces": (
                f"取自当前数据中实际有招生计划的 {len(provinces)} 个省级行政区，"
                "按院校数量排序，山东省优先。"
            ),
            "disciplines": (
                "山东计划表的「专业代码」是每所院校自己的专业序号，不是教育部专业代码，"
                "数据中也没有门类字段，因此按学科筛选目前无法命中任何志愿。"
                "可改用按专业名称筛选（原样或关键词）。"
                if not disciplines_available
                else "已建立专业到门类的映射，可按学科筛选。"
            ),
            "discipline_mapped_plans": mapped_plans,
        },
    }
