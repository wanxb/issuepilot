"""LLMCallLog（所有 LLM 调用的统一日志）。

每个 Agent 的每次 LLM 调用都记一条。用途：
    - 成本统计（按 agent / model / 是否 fallback 聚合）
    - 兜底切换分析（is_fallback 比例时序）
    - Eval Golden Set 抽样（success=true 且 cost 在 P50 附近的样本）
    - 错误归因（error_code 分布）

约定：
    - cost_usd 字段对 fallback provider（如 DeepSeek 中转）打 is_fallback=True 标记，
      该字段是中转层返回的"Anthropic 估算价"，非真实计费（详见 ARCHITECTURE §7.3）
"""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models._mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import AgentKind


class LLMCallLog(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "llm_call_logs"

    # 关联（任一可为 None，便于 ad-hoc 调用也能记录）
    issue_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("issues.id", ondelete="SET NULL"),
        nullable=True,
    )

    agent_kind: Mapped[AgentKind] = mapped_column(
        Enum(AgentKind, name="agent_kind", native_enum=True),
        nullable=False,
    )

    # provider 用 string 而非 enum：未来加新 provider 不用改 schema
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 用量
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # 性能
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # 关联 prompt 版本（便于 prompt A/B 测试归因）
    prompt_version: Mapped[str | None] = mapped_column(String(20), nullable=True)

    __table_args__ = (
        Index("ix_llm_call_logs_agent_kind", "agent_kind"),
        Index("ix_llm_call_logs_is_fallback", "is_fallback"),
        Index("ix_llm_call_logs_created_at", "created_at"),
        Index("ix_llm_call_logs_success", "success"),
    )

    def __repr__(self) -> str:
        marker = " [fallback]" if self.is_fallback else ""
        return f"<LLMCallLog {self.agent_kind.value} {self.provider}/{self.model}{marker}>"
