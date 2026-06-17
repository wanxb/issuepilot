"""CrawlJob（抓取任务执行记录）。

每次定时抓取或手动 URL 入口都创建一条记录，用于审计 + 看板抓取日志页。
失败时把 error 写到 stats 里。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models._mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import CrawlStatus, CrawlTrigger


class CrawlJob(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "crawl_jobs"

    trigger: Mapped[CrawlTrigger] = mapped_column(
        Enum(CrawlTrigger, name="crawl_trigger", native_enum=True),
        nullable=False,
    )
    status: Mapped[CrawlStatus] = mapped_column(
        Enum(CrawlStatus, name="crawl_status", native_enum=True),
        nullable=False,
        default=CrawlStatus.PENDING,
    )

    # 手动 URL 入口时记录用户传入的 URL，其它情况为 None
    input_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # 灵活统计字段：
    #   {"repos_crawled": N, "issues_found": N, "issues_new": N,
    #    "issues_skipped": N, "duration_seconds": float,
    #    "skipped_reasons": {reason: count}, "error": "..." }
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )

    def __repr__(self) -> str:
        return f"<CrawlJob {self.trigger.value} {self.status.value}>"
