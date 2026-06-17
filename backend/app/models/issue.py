"""Issue（GitHub Issue 主表）。

唯一性：repository_id + github_number。
状态机：详见 docs/DATA_MODEL.md §2 与 app/models/enums.py。
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models._mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import IssueSource, IssueStatus

if TYPE_CHECKING:
    from app.models.crawl_job import CrawlJob
    from app.models.evaluation import Evaluation
    from app.models.repository import Repository


class Issue(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "issues"

    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    github_number: Mapped[int] = mapped_column(Integer, nullable=False)
    github_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    github_url: Mapped[str] = mapped_column(String(500), nullable=False)

    # 原始内容
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    labels: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    author: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # 状态机
    status: Mapped[IssueStatus] = mapped_column(
        Enum(IssueStatus, name="issue_status", native_enum=True),
        nullable=False,
        default=IssueStatus.DISCOVERED,
    )
    source: Mapped[IssueSource] = mapped_column(
        Enum(IssueSource, name="issue_source", native_enum=True),
        nullable=False,
        default=IssueSource.CRAWL,
    )
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # 关联抓取任务（可空：手动入口不一定有 crawl_job）
    crawl_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("crawl_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # --- relationships ---
    repository: Mapped["Repository"] = relationship(lazy="joined")
    evaluation: Mapped["Evaluation | None"] = relationship(
        back_populates="issue",
        uselist=False,
        cascade="all, delete-orphan",
    )
    crawl_job: Mapped["CrawlJob | None"] = relationship(lazy="select")

    __table_args__ = (
        UniqueConstraint("repository_id", "github_number", name="uq_issues_repo_number"),
        Index("ix_issues_status", "status"),
        Index("ix_issues_source", "source"),
        Index("ix_issues_repository_id", "repository_id"),
    )

    def __repr__(self) -> str:
        return f"<Issue #{self.github_number} {self.status.value}>"
