"""API v1 router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    catalog,
    health_check,
    ingestion,
    meta,
    profiles,
    recommendations,
    schemes,
)

api_router = APIRouter()
api_router.include_router(meta.router)
api_router.include_router(profiles.router)
api_router.include_router(catalog.router)
api_router.include_router(recommendations.router)
api_router.include_router(schemes.router)
api_router.include_router(health_check.router)
api_router.include_router(ingestion.router)

__all__ = ["api_router"]
