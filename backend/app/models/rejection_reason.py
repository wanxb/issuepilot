"""RejectionReason（PR 被退回 / 关闭 / revert 的结构化原因）。

学习闭环的核心数据来源。三类 source（详见 DATA_MODEL.md §5.2）：
    - agent_c           Agent C REJECTED 时写入，此时 PR 尚未提交 → pr_id 为空
    - maintainer_review webhook：pull_request_review 触发
    - maintainer_close  webhook：pull_request closed（未 merge）触发

故 pr_id 可空，但 issue_id 必填（agent_c 退回时只有 issue 上下文）。
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models._mixins import TimestampMixin, UUIDPKMixin
from app.models._pg_enum import pg_enum
from app.models.enums import (
    AgentBAttribution,
    RejectionCategory,
    RejectionDimension,
    RejectionSeverity,
    RejectionSource,
)

if TYPE_CHECKING:
    from app.models.issue import Issue
    from app.models.pull_request import PullRequest
    from app.models.review_task import ReviewTask


class RejectionReason(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "rejection_reasons"

    issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("issues.id", ondelete="CASCADE"),
        nullable=False,
    )
    pr_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pull_requests.id", ondelete="CASCADE"),
        nullable=True,
    )
    review_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("review_tasks.id", ondelete="SET NULL"),
        nullable=True,
    )

    source: Mapped[RejectionSource] = mapped_column(
        pg_enum(RejectionSource, "rejection_source"),
        nullable=False,
    )
    category: Mapped[RejectionCategory] = mapped_column(
        pg_enum(RejectionCategory, "rejection_category"),
        nullable=False,
    )
    severity: Mapped[RejectionSeverity] = mapped_column(
        pg_enum(RejectionSeverity, "rejection_severity"),
        nullable=False,
    )
    dimension: Mapped[RejectionDimension | None] = mapped_column(
        pg_enum(RejectionDimension, "rejection_dimension"),
        nullable=True,
    )
    agent_b_attribution: Mapped[AgentBAttribution | None] = mapped_column(
        pg_enum(AgentBAttribution, "agent_b_attribution"),
        nullable=True,
    )

    detail: Mapped[str] = mapped_column(Text, nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    referenced_files: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True,
    )
    # [{"file": str, "start": int, "end": int}, ...]
    referenced_lines: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True,
    )

    # 分类器元数据（RejectionClassifier Agent / Agent C / null）
    classified_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    classifier_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True,
    )

    # relationships
    issue: Mapped["Issue"] = relationship(lazy="select")
    pull_request: Mapped["PullRequest | None"] = relationship(lazy="select")
    review_task: Mapped["ReviewTask | None"] = relationship(lazy="select")

    __table_args__ = (
        Index("ix_rejection_reasons_issue_id", "issue_id"),
        Index("ix_rejection_reasons_pr_id", "pr_id"),
        Index("ix_rejection_reasons_source", "source"),
        Index("ix_rejection_reasons_category", "category"),
        Index("ix_rejection_reasons_attribution", "agent_b_attribution"),
    )

    def __repr__(self) -> str:
        return f"<RejectionReason {self.source.value}/{self.category.value}>"
