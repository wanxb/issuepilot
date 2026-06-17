"""Evaluation（Agent A 评估结果）。

1:1 关联 issue。Schema 来自 docs/AGENT_DESIGN.md §Agent A。
dimensions 用 JSONB 存（5 个维度，结构稳定但每个 sub-dict 有 score/comment）。
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models._mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import IssueDifficulty

if TYPE_CHECKING:
    from app.models.issue import Issue


class Evaluation(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "evaluations"

    issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("issues.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # Agent A 输出（详见 AGENT_DESIGN §Agent A）
    total_score: Mapped[float] = mapped_column(Float, nullable=False)
    difficulty: Mapped[IssueDifficulty] = mapped_column(
        Enum(IssueDifficulty, name="issue_difficulty", native_enum=True),
        nullable=False,
    )
    estimated_hours: Mapped[float] = mapped_column(Float, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    is_worth_developing: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # 5 个维度 (clarity, feasibility, value, repo_activity, context_sufficiency)
    # 结构：{"clarity": {"score": 8.5, "comment": "..."}, ...}
    dimensions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # LLM 元数据
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False, default="v1.0")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- relationships ---
    issue: Mapped["Issue"] = relationship(back_populates="evaluation")

    def __repr__(self) -> str:
        return f"<Evaluation issue={self.issue_id} score={self.total_score:.1f}>"
