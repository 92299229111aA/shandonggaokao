"""Health Check endpoints.

Spec: HealthCheckRequest 指定 scheme version. The report is diagnostic only.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.healthcheck.schemas import HealthCheckDTO, HealthCheckRequest
from app.healthcheck.service import HealthCheckService

router = APIRouter(tags=["health-check"])


@router.post(
    "/schemes/{scheme_id}/health-check",
    response_model=HealthCheckDTO,
    status_code=status.HTTP_201_CREATED,
    summary="志愿体检（只诊断，不修改方案）",
)
def run_health_check(
    scheme_id: int,
    payload: HealthCheckRequest,
    session: Annotated[Session, Depends(get_session)],
):
    dto = HealthCheckService(session).run(
        scheme_id=scheme_id,
        version_no=payload.version,
        recheck_eligibility=payload.recheck_eligibility,
        persist=payload.persist,
    )
    session.commit()
    return dto


@router.get(
    "/schemes/{scheme_id}/health-checks",
    response_model=list[HealthCheckDTO],
    summary="该方案的历史体检报告",
)
def list_health_checks(scheme_id: int, session: Annotated[Session, Depends(get_session)]):
    return HealthCheckService(session).list_for_scheme(scheme_id)


@router.get("/health-checks/{check_id}", response_model=HealthCheckDTO, summary="体检报告详情")
def get_health_check(check_id: int, session: Annotated[Session, Depends(get_session)]):
    return HealthCheckService(session).get(check_id)
