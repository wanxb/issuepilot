"""CrawlTargetService（2.2）—— crawl_targets 表的 CRUD 与状态更新。

调度集成（scheduler.py）按 list_enabled() 注册 APScheduler 每个 target 一个 job；
任务完成时 worker 调 update_run_result 写回 last_run_at / last_status。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crawl_target import CrawlTarget
from app.services.crawl_sources import (
    SUPPORTED_SOURCES,
    SourceConfigError,
    validate_spec,
)

log = structlog.get_logger(__name__)


class CrawlTargetError(Exception):
    code: str = "CRAWL_TARGET_ERROR"


class CrawlTargetNotFound(CrawlTargetError):
    code = "CRAWL_TARGET_NOT_FOUND"


class CrawlTargetInvalid(CrawlTargetError):
    code = "CRAWL_TARGET_INVALID"


# 5-field cron sanity check（APScheduler 自己也会校验，这里只挡明显错的输入）
def _check_cron(cron: str) -> None:
    parts = (cron or "").split()
    if len(parts) != 5:
        raise CrawlTargetInvalid(
            f"cron must be 5-field expression, got {cron!r}",
        )


class CrawlTargetService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ---- CRUD ----

    async def create(
        self,
        *,
        name: str,
        source: str,
        spec: dict[str, Any],
        cron: str,
        enabled: bool = True,
    ) -> CrawlTarget:
        if not name or len(name) > 100:
            raise CrawlTargetInvalid("name must be 1..100 chars")
        if source not in SUPPORTED_SOURCES:
            raise CrawlTargetInvalid(
                f"source must be one of {SUPPORTED_SOURCES}; got {source!r}",
            )
        try:
            validate_spec(source, spec)
        except SourceConfigError as e:
            raise CrawlTargetInvalid(str(e)) from e
        _check_cron(cron)

        target = CrawlTarget(
            name=name, source=source, spec=spec, cron=cron, enabled=enabled,
        )
        self._s.add(target)
        await self._s.flush()
        return target

    async def get(self, target_id: uuid.UUID) -> CrawlTarget:
        target = await self._s.get(CrawlTarget, target_id)
        if target is None:
            raise CrawlTargetNotFound(str(target_id))
        return target

    async def get_by_name(self, name: str) -> CrawlTarget:
        stmt = select(CrawlTarget).where(CrawlTarget.name == name)
        target = (await self._s.execute(stmt)).scalar_one_or_none()
        if target is None:
            raise CrawlTargetNotFound(name)
        return target

    async def list_all(self) -> list[CrawlTarget]:
        rows = (await self._s.execute(
            select(CrawlTarget).order_by(CrawlTarget.created_at),
        )).scalars().all()
        return list(rows)

    async def list_enabled(self) -> list[CrawlTarget]:
        rows = (await self._s.execute(
            select(CrawlTarget)
            .where(CrawlTarget.enabled.is_(True))
            .order_by(CrawlTarget.created_at),
        )).scalars().all()
        return list(rows)

    async def set_enabled(
        self, target_id: uuid.UUID, *, enabled: bool,
    ) -> CrawlTarget:
        target = await self.get(target_id)
        target.enabled = enabled
        await self._s.flush()
        return target

    async def delete(self, target_id: uuid.UUID) -> None:
        target = await self.get(target_id)
        await self._s.delete(target)
        await self._s.flush()

    # ---- 运行状态写回 ----

    async def update_run_result(
        self,
        target_id: uuid.UUID,
        *,
        status: str,           # "succeeded" / "failed" / "partial"
        error: str | None = None,
        next_run_at: datetime | None = None,
    ) -> CrawlTarget:
        target = await self.get(target_id)
        target.last_run_at = datetime.now(timezone.utc)
        target.last_status = status[:20]
        target.last_error = (error or "")[:500] or None
        if next_run_at is not None:
            target.next_run_at = next_run_at
        await self._s.flush()
        return target
