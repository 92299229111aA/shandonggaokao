"""Scheme endpoints: create, edit, re-optimize, version history, export."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.schemes.schemas import (
    ExportResponse,
    OptimizeRequest,
    SchemeAddItems,
    SchemeCreate,
    SchemeDTO,
    SchemeLockUpdate,
    SchemeOrderUpdate,
    SchemeVersionDTO,
)
from app.schemes.service import SchemeService

router = APIRouter(prefix="/schemes", tags=["schemes"])

VERSION_HEADER = "每次修改都会产生新的 scheme_version，旧版本始终可读"


@router.post(
    "",
    response_model=SchemeDTO,
    status_code=status.HTTP_201_CREATED,
    summary="生成志愿方案（" + VERSION_HEADER + "）",
)
def create_scheme(payload: SchemeCreate, session: Annotated[Session, Depends(get_session)]):
    dto = SchemeService(session).create(payload)
    session.commit()
    return dto


@router.get("/{scheme_id}", response_model=SchemeDTO, summary="当前方案（最新版本）")
def get_scheme(
    scheme_id: int,
    session: Annotated[Session, Depends(get_session)],
    version_no: Annotated[int | None, Query(description="读取指定历史版本")] = None,
):
    service = SchemeService(session)
    return service.get(scheme_id) if version_no is None else service.get_version(scheme_id, version_no)


@router.get("/{scheme_id}/versions", response_model=list[SchemeVersionDTO], summary="方案历史版本")
def list_scheme_versions(scheme_id: int, session: Annotated[Session, Depends(get_session)]):
    return SchemeService(session).list_versions(scheme_id)


@router.patch(
    "/{scheme_id}/order",
    response_model=SchemeDTO,
    summary="调整顺序 / 锁定（乐观锁，冲突返回 409）",
)
def update_scheme_order(
    scheme_id: int, payload: SchemeOrderUpdate, session: Annotated[Session, Depends(get_session)]
):
    dto = SchemeService(session).update_order(scheme_id, payload)
    session.commit()
    return dto


@router.post("/{scheme_id}/items", response_model=SchemeDTO, summary="加入志愿")
def add_scheme_items(
    scheme_id: int, payload: SchemeAddItems, session: Annotated[Session, Depends(get_session)]
):
    dto = SchemeService(session).add_items(scheme_id, payload.version, payload.recommendation_item_ids)
    session.commit()
    return dto


@router.delete("/{scheme_id}/items/{position}", response_model=SchemeDTO, summary="删除指定位置的志愿")
def remove_scheme_item(
    scheme_id: int,
    position: int,
    session: Annotated[Session, Depends(get_session)],
    version: Annotated[int, Query(ge=1, description="当前 lock_version，用于乐观锁")],
):
    dto = SchemeService(session).remove_item(scheme_id, version, position)
    session.commit()
    return dto


@router.put("/{scheme_id}/items/{position}/lock", response_model=SchemeDTO, summary="锁定 / 解锁志愿")
def set_scheme_item_lock(
    scheme_id: int,
    position: int,
    payload: SchemeLockUpdate,
    session: Annotated[Session, Depends(get_session)],
):
    dto = SchemeService(session).set_lock(scheme_id, payload.version, position, payload.locked)
    session.commit()
    return dto


@router.post(
    "/{scheme_id}/optimize",
    response_model=SchemeDTO,
    summary="重新优化（保留锁定志愿，UNKNOWN 不计入保底）",
)
def optimize_scheme(
    scheme_id: int, payload: OptimizeRequest, session: Annotated[Session, Depends(get_session)]
):
    dto = SchemeService(session).optimize(scheme_id, payload)
    session.commit()
    return dto


@router.get("/{scheme_id}/export", response_model=ExportResponse, summary="导出方案（CSV/Excel 用数据）")
def export_scheme(
    scheme_id: int,
    session: Annotated[Session, Depends(get_session)],
    version_no: Annotated[int | None, Query(description="缺省导出当前版本")] = None,
):
    return SchemeService(session).export(scheme_id, version_no)
