"""Official-data upload endpoints.

    POST /api/v1/ingestion/score-rank  一分一段表
    POST /api/v1/ingestion/plans       当年招生计划
    POST /api/v1/ingestion/outcomes    历年投档/录取结果
    GET  /api/v1/ingestion/templates   CSV 表头模板

All three accept multipart/form-data and default to `dry_run=true`, so a file can
be validated before anything is written.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.common.errors import RuleViolationError
from app.ingestion.importer import TEMPLATES, ImportKind, ImportReport, IngestionService

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

MAX_UPLOAD_BYTES = 64 * 1024 * 1024  # 64 MB: a full 投档结果 table is a few MB


def _import(
    *,
    kind: ImportKind,
    session: Session,
    file: UploadFile,
    snapshot_id: int | None,
    cycle_id: int | None,
    publisher: str | None,
    url: str | None,
    published_on: date | None,
    dry_run: bool,
) -> ImportReport:
    content = file.file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise RuleViolationError(
            f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 上限，请拆分后分批上传"
        )
    report = IngestionService(session).import_file(
        kind=kind,
        content=content,
        filename=file.filename or f"{kind.value}.csv",
        snapshot_id=snapshot_id,
        cycle_id=cycle_id,
        publisher=publisher,
        url=url,
        published_on=published_on,
        dry_run=dry_run,
    )
    if not dry_run and report.ok:
        session.commit()
    return report


@router.post(
    "/score-rank",
    response_model=ImportReport,
    summary="导入一分一段表（决定位次百分位标准化，是 ACS 的前提）",
)
def import_score_rank(
    session: Annotated[Session, Depends(get_session)],
    file: Annotated[UploadFile, File(description="CSV：exam_year,score,same_score_count,cumulative_rank")],
    dry_run: Annotated[bool, Query(description="只校验不写库")] = True,
    publisher: Annotated[str | None, Form()] = None,
    url: Annotated[str | None, Form()] = None,
    published_on: Annotated[date | None, Form()] = None,
) -> ImportReport:
    return _import(
        kind=ImportKind.SCORE_RANK, session=session, file=file,
        snapshot_id=None, cycle_id=None, publisher=publisher, url=url,
        published_on=published_on, dry_run=dry_run,
    )


@router.post(
    "/plans",
    response_model=ImportReport,
    summary="导入当年招生计划（需先有 PUBLISHED 数据快照与院校基础数据）",
)
def import_plans(
    session: Annotated[Session, Depends(get_session)],
    file: Annotated[UploadFile, File(description="CSV：university_name,plan_name,plan_count,tuition_fee,subject_requirement")],
    dry_run: Annotated[bool, Query(description="只校验不写库")] = True,
    snapshot_id: Annotated[int | None, Form()] = None,
    cycle_id: Annotated[int | None, Form()] = None,
    publisher: Annotated[str | None, Form()] = None,
    url: Annotated[str | None, Form()] = None,
    published_on: Annotated[date | None, Form()] = None,
) -> ImportReport:
    return _import(
        kind=ImportKind.PLANS, session=session, file=file,
        snapshot_id=snapshot_id, cycle_id=cycle_id, publisher=publisher, url=url,
        published_on=published_on, dry_run=dry_run,
    )


@router.post(
    "/outcomes",
    response_model=ImportReport,
    summary="导入历年投档/录取结果（ACS 的历史依据）",
)
def import_outcomes(
    session: Annotated[Session, Depends(get_session)],
    file: Annotated[UploadFile, File(description="CSV：university_name,plan_name,exam_year,min_rank")],
    dry_run: Annotated[bool, Query(description="只校验不写库")] = True,
    snapshot_id: Annotated[int | None, Form()] = None,
    cycle_id: Annotated[int | None, Form()] = None,
    publisher: Annotated[str | None, Form()] = None,
    url: Annotated[str | None, Form()] = None,
    published_on: Annotated[date | None, Form()] = None,
) -> ImportReport:
    return _import(
        kind=ImportKind.OUTCOMES, session=session, file=file,
        snapshot_id=snapshot_id, cycle_id=cycle_id, publisher=publisher, url=url,
        published_on=published_on, dry_run=dry_run,
    )


@router.get("/templates", summary="CSV 表头模板（含字段说明）")
def templates() -> dict:
    return {
        "templates": TEMPLATES,
        "notes": {
            "score-rank": (
                "一分一段表。cumulative_rank 是累计人数（即位次）；"
                "系统会用最后一行推导当年考生总人数，用于跨年位次百分位标准化。"
                "支持逗号/制表符分隔，支持 UTF-8 与 GBK 编码。"
            ),
            "plans": (
                "当年招生计划。院校必须已存在于院校库（reference.universities），"
                "否则该行会被拒绝而不是猜测创建。选科要求列可填空，留空表示不限。"
            ),
            "outcomes": (
                "历年投档结果。按「院校名称 + 专业名称」匹配已导入的招生计划序列；"
                "匹配不到的行会被拒绝，请先导入招生计划。"
            ),
        },
        "dry_run": "三个接口默认 dry_run=true，只校验不写库；确认无误后加 ?dry_run=false 正式导入。",
    }
