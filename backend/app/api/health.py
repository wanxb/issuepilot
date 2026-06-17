"""健康检查端点。

    /healthz  — 进程存活（liveness），无外部依赖
    /readyz   — 依赖就绪（readiness），检查 DB + Redis
"""
from __future__ import annotations

from typing import Literal

import redis.asyncio as redis_async
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.config import get_settings
from app.db.database import ping as db_ping

router = APIRouter(tags=["health"])


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"
    app: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    db: bool
    redis: bool


@router.get("/healthz", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    settings = get_settings()
    return LivenessResponse(app=settings.app_name)


@router.get("/readyz")
async def readiness() -> JSONResponse:
    db_ok = False
    redis_ok = False
    try:
        db_ok = await db_ping()
    except Exception:
        db_ok = False

    settings = get_settings()
    try:
        client = redis_async.from_url(settings.redis_url)
        pong = await client.ping()
        redis_ok = bool(pong)
        await client.aclose()
    except Exception:
        redis_ok = False

    payload = ReadinessResponse(
        status="ready" if (db_ok and redis_ok) else "not_ready",
        db=db_ok,
        redis=redis_ok,
    )
    code = status.HTTP_200_OK if (db_ok and redis_ok) else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=payload.model_dump(), status_code=code)
