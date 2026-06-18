"""PROutcome（PR 生命周期事件流水）。

不是状态机——每条记录代表 PR 上发生过的一个事件（submit/review/comment/
push/merge/close/revert）。复盘 + Phase 3 学习闭环用。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models._mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import PROutcomeEventType

if TYPE_CHECKING:
    from app.models.pull_request import PullRequest


class PROutcome(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "pr_outcomes"

    pr_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pull_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[PROutcomeEventType] = mapped_column(
        Enum(PROutcomeEventType, name="pr_outcome_event_type", native_enum=True),
        nullable=False,
    )
    actor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    # relationships
    pull_request: Mapped["PullRequest"] = relationship(lazy="select")

    __table_args__ = (
        Index("ix_pr_outcomes_pr_id", "pr_id"),
        Index("ix_pr_outcomes_pr_id_occurred_at", "pr_id", "occurred_at"),
        Index("ix_pr_outcomes_event_type", "event_type"),
    )

    def __repr__(self) -> str:
        return f"<PROutcome pr={self.pr_id} {self.event_type.value}>"
