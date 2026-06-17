"""WebSocket + REST dev-logs 端点。

    GET  /api/v1/dev-tasks/{dev_task_id}/logs  — 历史日志（REST，最多 200 条）
    WS   /ws/dev-tasks/{dev_task_id}/logs      — 实时日志推送（Redis PubSub）

WebSocket 协议：
    1. 连接建立后，先发送所有历史日志（JSON array）
    2. 订阅 Redis channel dev:logs:{dev_task_id}
    3. 每条新日志作为 JSON string 推送给客户端
    4. 客户端断开时取消订阅

日志条目格式（JSON object）：
    {"id": "...", "level": "info", "step": "implement", "message": "...", "created_at": "..."}
"""
from __future__ import annotations

import json
import uuid

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.config import get_settings
from app.db.database import AsyncSessionLocal
from app.models.dev_log import DevLog

log = structlog.get_logger(__name__)

router = APIRouter(tags=["dev-logs"])

_DEV_LOGS_CHANNEL_PREFIX = "dev:logs:"


# ---------------------------------------------------------------------------
# REST: 历史日志
# ---------------------------------------------------------------------------


@router.get("/api/v1/dev-tasks/{dev_task_id}/logs")
async def get_dev_logs(
    dev_task_id: uuid.UUID,
    limit: int = 200,
) -> list[dict]:
    """返回指定 DevTask 的历史日志，按 created_at 升序。"""
    async with AsyncSessionLocal() as s:
        stmt = (
            select(DevLog)
            .where(DevLog.dev_task_id == dev_task_id)
            .order_by(DevLog.created_at.asc())
            .limit(limit)
        )
        rows = (await s.execute(stmt)).scalars().all()
    return [_log_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# WebSocket: 实时日志
# ---------------------------------------------------------------------------


@router.websocket("/ws/dev-tasks/{dev_task_id}/logs")
async def ws_dev_logs(websocket: WebSocket, dev_task_id: uuid.UUID) -> None:
    """WebSocket 端点：发送历史日志后订阅 Redis PubSub 实时推送。"""
    await websocket.accept()
    settings = get_settings()
    channel = f"{_DEV_LOGS_CHANNEL_PREFIX}{dev_task_id}"

    # 先发送历史日志
    try:
        async with AsyncSessionLocal() as s:
            stmt = (
                select(DevLog)
                .where(DevLog.dev_task_id == dev_task_id)
                .order_by(DevLog.created_at.asc())
                .limit(500)
            )
            rows = (await s.execute(stmt)).scalars().all()
        history = [_log_to_dict(r) for r in rows]
        await websocket.send_text(json.dumps({"type": "history", "logs": history}))
    except Exception as exc:
        log.warning("ws_dev_logs.history_failed", error=str(exc))

    # 订阅 Redis PubSub
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = redis_client.pubsub()
    try:
        await pubsub.subscribe(channel)
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    await websocket.send_text(
                        json.dumps({"type": "log", "data": json.loads(message["data"])})
                    )
                except Exception:
                    break
    except WebSocketDisconnect:
        log.debug("ws_dev_logs.disconnect", dev_task_id=str(dev_task_id))
    except Exception as exc:
        log.warning("ws_dev_logs.error", error=str(exc))
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await redis_client.aclose()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _log_to_dict(entry: DevLog) -> dict:
    return {
        "id": str(entry.id),
        "level": entry.level.value,
        "step": entry.step.value,
        "message": entry.message,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }
