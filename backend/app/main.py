"""FastAPI application factory.

Routers stay thin: they validate DTOs and call services. All policy numbers come
from the database, never from constants in this package.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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

    # 桌面版：同一个进程既提供 API 也提供前端静态文件。
    #
    # 这样 Electron 只需要加载一个本地 URL，前端用相对路径调 API ——
    # 没有 CORS、没有 file:// 的路径问题、也只有一个后端进程要管。
    static_dir = Path(settings.static_dir) if settings.static_dir else None
    if static_dir and (static_dir / "index.html").exists():
        logger.info("serving frontend from %s", static_dir)

        # 带哈希的构建产物可以长缓存；其余短缓存，方便升级后立刻生效
        if (static_dir / "_next").is_dir():
            app.mount("/_next", StaticFiles(directory=static_dir / "_next"), name="next-assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> FileResponse:
            # 先当真实文件处理，找不到就回退到 index.html 交给前端路由
            root = static_dir.resolve()
            candidate = (root / full_path).resolve()
            # 静态导出是目录结构（schemes/index.html），目录要落回它自己的 index.html，
            # 否则每个路由都拿到根页面，丢失各自预加载的分包。
            if candidate.is_dir():
                candidate = candidate / "index.html"
            if full_path and candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate)
            return FileResponse(root / "index.html")
    else:

        @app.get("/", include_in_schema=False)
        def root() -> dict:
            return {"service": settings.app_name, "version": __version__, "docs": "/docs"}

    return app


app = create_app()
