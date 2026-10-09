"""Meta endpoints: which data version is live, and service health."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import __version__
from app.admission.catalog_service import CatalogService
from app.admission.schemas import AdmissionCycleDTO, DatasetMetaDTO
from app.api.deps import get_session
from app.config import get_settings

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/health", summary="服务健康检查")
def health(session: Annotated[Session, Depends(get_session)]) -> dict:
    database = "up"
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        database = f"down: {type(exc).__name__}"
    return {
        "status": "ok" if database == "up" else "degraded",
        "version": __version__,
        "environment": get_settings().environment,
        "database": database,
    }


@router.get("/admission-cycles", response_model=list[AdmissionCycleDTO], summary="可用招生周期")
def admission_cycles(session: Annotated[Session, Depends(get_session)]):
    """Every policy number the UI needs (including the volunteer limit) comes from here."""
    return CatalogService(session).list_cycles()


@router.get("/dataset", response_model=DatasetMetaDTO, summary="当前生效的数据集与模型版本")
def dataset(
    session: Annotated[Session, Depends(get_session)],
    cycle_id: Annotated[int | None, Query(description="缺省时使用最新的活跃周期")] = None,
):
    return CatalogService(session).dataset_meta(cycle_id=cycle_id)
