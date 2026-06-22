"""FastAPI 应用入口。

里程碑 1.1：只有健康检查端点。后续里程碑追加 issues / crawl_jobs / pull_requests 等路由。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.api.crawl_jobs import router as crawl_jobs_router
from app.api.dashboard import router as dashboard_router
from app.api.health import router as health_router
from app.api.issues import router as issues_router
from app.api.webhooks import router as webhooks_router
from app.api.ws import router as ws_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.database import engine
from app.scheduler import init_scheduler, shutdown_scheduler

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level, env=settings.env)
    log.info("app.startup", env=settings.env, app=settings.app_name)
    init_scheduler()       # 2.3: 启动 APScheduler in-process scheduler
    try:
        yield
    finally:
        shutdown_scheduler()
        await engine.dispose()
        log.info("app.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="IssuePilot API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(issues_router)
    app.include_router(crawl_jobs_router)
    app.include_router(webhooks_router)
    app.include_router(ws_router)
    app.include_router(admin_router)
    app.include_router(dashboard_router)
    return app


app = create_app()
