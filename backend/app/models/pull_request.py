"""PullRequest（已提交的 GitHub PR 记录）。

1 issue 通常对应 1 个 PR。close 后人工决策若选「重新开发」则会有新 dev_task
新 review_task，最终可能产生新 PR（这种情况下 issue_id 不再 unique）。
故 issue_id 加索引但不 unique；用 github_pr_url unique 防重复写入。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models._mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import PRFinalOutcome, PullRequestStatus

if TYPE_CHECKING:
    from app.models.issue import Issue
    from app.models.review_task import ReviewTask


class PullRequest(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "pull_requests"

    issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("issues.id", ondelete="CASCADE"),
        nullable=False,
    )
    review_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("review_tasks.id", ondelete="SET NULL"),
        nullable=True,
    )

    # GitHub 元数据
    github_pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    github_pr_url: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 推送上下文
    head_repo: Mapped[str] = mapped_column(String(255), nullable=False)  # forked repo
    head_branch: Mapped[str] = mapped_column(String(200), nullable=False)
    base_repo: Mapped[str] = mapped_column(String(255), nullable=False)  # upstream
    base_branch: Mapped[str] = mapped_column(String(200), nullable=False, default="main")

    # 状态
    status: Mapped[PullRequestStatus] = mapped_column(
        Enum(PullRequestStatus, name="pull_request_status", native_enum=True),
        nullable=False,
        default=PullRequestStatus.OPEN,
    )
    final_outcome: Mapped[PRFinalOutcome | None] = mapped_column(
        Enum(PRFinalOutcome, name="pr_final_outcome", native_enum=True),
        nullable=True,
    )
    close_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 时间戳（与 GitHub 上的事件对齐）
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    merged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # GitHub 侧 actor
    merger_login: Mapped[str | None] = mapped_column(String(100), nullable=True)
    closer_login: Mapped[str | None] = mapped_column(String(100), nullable=True)
    merge_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # relationships
    issue: Mapped["Issue"] = relationship(lazy="select")
    review_task: Mapped["ReviewTask | None"] = relationship(lazy="select")

    __table_args__ = (
        UniqueConstraint("github_pr_url", name="uq_pull_requests_url"),
        Index("ix_pull_requests_issue_id", "issue_id"),
        Index("ix_pull_requests_status", "status"),
        Index("ix_pull_requests_final_outcome", "final_outcome"),
    )

    def __repr__(self) -> str:
        return f"<PullRequest #{self.github_pr_number} {self.status.value}>"
