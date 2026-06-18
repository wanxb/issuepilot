"""ReviewTask（Agent C 单次评审尝试）。

每次 Agent B 成功产出 → Agent C 消费 → 一条 review_task。
同一 dev_task 通常 1:1 一次评审；REJECTED 退回 Agent B 后会新建下一次
review_task（attempt_number 自增）。

verdict / dimensions / pr_title / pr_body 字段都对应
docs/AGENT_DESIGN.md §Agent C 输出 schema。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models._mixins import TimestampMixin, UUIDPKMixin
from app.models._pg_enum import pg_enum
from app.models.enums import ReviewTaskStatus, ReviewVerdict

if TYPE_CHECKING:
    from app.models.dev_task import DevTask
    from app.models.issue import Issue


class ReviewTask(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "review_tasks"

    issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("issues.id", ondelete="CASCADE"),
        nullable=False,
    )
    dev_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dev_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    status: Mapped[ReviewTaskStatus] = mapped_column(
        pg_enum(ReviewTaskStatus, "review_task_status"),
        nullable=False,
        default=ReviewTaskStatus.PENDING,
    )

    # Agent C 输出（COMPLETED 时填充）
    verdict: Mapped[ReviewVerdict | None] = mapped_column(
        pg_enum(ReviewVerdict, "review_verdict"),
        nullable=True,
    )
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 5 维：correctness / test_coverage / code_style / security / pr_description
    # {dim: {"score": int, "passed": bool, "comment": str}}
    dimensions: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    pr_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pr_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    overall_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 失败路径（status=FAILED 时填充，例如 tool 未调 / schema validation）
    failure_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    # LLM 元数据
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False, default="v1.0")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # relationships
    issue: Mapped["Issue"] = relationship(lazy="select")
    dev_task: Mapped["DevTask"] = relationship(lazy="select")

    __table_args__ = (
        Index("ix_review_tasks_issue_id", "issue_id"),
        Index("ix_review_tasks_dev_task_id", "dev_task_id"),
        Index("ix_review_tasks_status", "status"),
    )

    def __repr__(self) -> str:
        v = self.verdict.value if self.verdict else "?"
        return f"<ReviewTask issue={self.issue_id} attempt={self.attempt_number} v={v}>"
