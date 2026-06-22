"""CrawlTarget（定时抓取配置项）— 2.2。

一条记录 = 一个独立的定时抓取任务。scheduler 启动时按 cron 注册 APScheduler
job；任务触发时按 source 选择对应的抓取策略，把发现的 repo URL 喂给
CrawlerService。

source 取值：
    - github_trending  spec = {"language": "python", "since": "daily"}
    - github_search    spec = {"q": "label:good-first-issue language:python"}
    - explicit_repos   spec = {"repos": ["owner/r1", "owner/r2"]}
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models._mixins import TimestampMixin, UUIDPKMixin


class CrawlTarget(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "crawl_targets"

    # 用户给的简短名字（"trending-python-daily"），便于日志 / CLI / UI 引用
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    source: Mapped[str] = mapped_column(String(50), nullable=False)
    # JSONB spec：每种 source 的参数；schema 在 service 层校验
    spec: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )

    # 标准 5-field cron expression（minute hour dom month dow），UTC
    cron: Mapped[str] = mapped_column(String(50), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true",
    )

    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        Index("ix_crawl_targets_enabled", "enabled"),
        Index("ix_crawl_targets_source", "source"),
    )

    def __repr__(self) -> str:
        return f"<CrawlTarget {self.name}({self.source}) cron={self.cron} enabled={self.enabled}>"
