"""FastAPI application factory.

Routers stay thin: they validate DTOs and call services. All policy numbers come
from the database, never from constants in this package.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1 import api_router
from app.common.errors import ErrorResponse, register_exception_handlers
from app.config import get_settings

logger = logging.getLogger(__name__)

DESCRIPTION = """
山东高考智能志愿系统 V1 —— 模块化单体后端。

**架构约束**

* Eligibility（能不能报）/ ACS（历史录取安全程度）/ Preference（用户有多想去）三层严格分离。
* ACS 是 0~100 的**录取安全指数**，V1 **未做概率校准**，任何接口都不返回"录取概率"。
* FAIL 的志愿不会进入正式推荐；UNKNOWN 可以进入候选池，但不会被计入保底数量。
* 志愿数量上限等政策数字一律来自 `admission_cycles`，不写死在代码或前端。
* 每个推荐会话锁定 profile/preference/dataset/policy/model 版本，历史可重放。
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    logger.info("starting %s v%s (%s)", settings.app_name, __version__, settings.environment)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        responses={
            422: {"model": ErrorResponse, "description": "参数校验失败"},
            409: {"model": ErrorResponse, "description": "版本冲突（乐观锁）"},
            404: {"model": ErrorResponse, "description": "资源不存在"},
        },
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {"service": settings.app_name, "version": __version__, "docs": "/docs"}

    return app


app = create_app()
