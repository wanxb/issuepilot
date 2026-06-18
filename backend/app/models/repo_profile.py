"""RepoProfile（Agent D 生成的仓库画像）。

1:1 关联 repository。TTL 90 天，过期由 profile_worker 重新生成。
Agent B / Agent C system prompt 在启动时注入本表内容；缺失或过期时
Agent B 走自学习降级路径（不阻塞主流程）。

Schema 严格对齐 docs/AGENT_DESIGN.md §Agent D 输出 + DATA_MODEL.md §5.4。
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models._mixins import TimestampMixin, UUIDPKMixin
from app.models._pg_enum import pg_enum
from app.models.enums import ProfileQuality

if TYPE_CHECKING:
    from app.models.repository import Repository


class RepoProfile(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "repo_profiles"

    repo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # Agent D 输出（与 AgentDOutput schema 对齐）
    test_command: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    install_command: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    lint_command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    code_style_notes: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    contributing_summary: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    forbidden_patterns: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True,
    )
    pr_title_convention: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # 形如 [{url, title_pattern, diff_style_note}, ...]，最多 3 条
    merged_pr_examples: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True,
    )

    profile_quality: Mapped[ProfileQuality] = mapped_column(
        pg_enum(ProfileQuality, "profile_quality"),
        nullable=False,
        default=ProfileQuality.MEDIUM,
    )
    quality_reason: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    # 生命周期
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    forced_refresh_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False,
    )

    # LLM 元数据
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False, default="v1.0")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # relationships
    repository: Mapped["Repository"] = relationship(lazy="select")

    __table_args__ = (
        Index("ix_repo_profiles_expires_at", "expires_at"),
        Index("ix_repo_profiles_profile_quality", "profile_quality"),
    )

    def __repr__(self) -> str:
        return f"<RepoProfile repo={self.repo_id} q={self.profile_quality.value}>"
