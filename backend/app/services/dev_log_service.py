"""DevLogService —— DevLog 写 DB + Redis PubSub 发布。

使用方式：
    await dev_log_service.append(session, redis_client, dev_task_id=..., ...)

Redis channel 命名约定：dev:logs:{dev_task_id}
WebSocket handler 订阅此 channel 向前端推送实时日志。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dev_log import DevLog
from app.models.enums import DevLogLevel, DevLogStep

log = structlog.get_logger(__name__)


def _redis_channel(dev_task_id: UUID) -> str:
    return f"dev:logs:{dev_task_id}"


async def append(
    session: AsyncSession,
    redis_client: object,
    *,
    dev_task_id: UUID,
    level: DevLogLevel,
    step: DevLogStep,
    message: str,
) -> DevLog:
    """写一条 DevLog 并发布到 Redis PubSub channel。

    session.flush() 后 DevLog.created_at 由数据库填充（server_default=now()）。
    Redis publish 失败不影响 DB 写入（best-effort）。
    """
    entry = DevLog(
        dev_task_id=dev_task_id,
        level=level,
        step=step,
        message=message,
    )
    session.add(entry)
    await session.flush()

    # Redis publish（best-effort）
    try:
        payload = json.dumps(
            {
                "id": str(entry.id),
                "level": level.value,
                "step": step.value,
                "message": message,
                "created_at": (
                    entry.created_at.isoformat()
                    if entry.created_at
                    else datetime.now(timezone.utc).isoformat()
                ),
            }
        )
        await redis_client.publish(_redis_channel(dev_task_id), payload)  # type: ignore[attr-defined]
    except Exception as exc:
        log.debug("dev_log.redis_publish_failed", error=str(exc))

    return entry
