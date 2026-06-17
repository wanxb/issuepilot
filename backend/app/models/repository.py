"""Repository（GitHub 仓库元数据缓存）。

每个仓库唯一 by full_name。元数据由 CrawlerService 在抓取时刷新。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models._mixins import TimestampMixin, UUIDPKMixin


class Repository(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "repositories"

    full_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    owner: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # GitHub 元数据快照
    github_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    primary_language: Mapped[str | None] = mapped_column(String(50), nullable=True)
    languages: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    topics: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    stars: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    forks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    open_issues_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # 活跃度（Agent A 评估 repo_activity 维度需要）
    last_commit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    merged_prs_last_30d: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    open_prs_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # 本系统状态
    last_crawled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        Index("ix_repositories_primary_language", "primary_language"),
        Index("ix_repositories_stars", "stars"),
    )

    def __repr__(self) -> str:
        return f"<Repository {self.full_name}>"
