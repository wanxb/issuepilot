"""EvalSample（Eval Golden Set 标注，3.1）。

把 issues / pull_requests / rejection_reasons 中有学习价值的样本显式打标，
留作 prompt A/B 测试、离线评估、few-shot 反例的"训练集"。

sample_kind：
    - positive_merged_clean     真实 MERGED_CLEAN PR，正样本
    - negative_agent_b_fault    agent_b_attribution=yes + severity=blocker，Agent B 反例
    - style_pattern             高频 style_mismatch，用于强化 "学习 CONTRIBUTING.md"
    - tricky_evaluation         Agent A 评估难点（边缘分），用于 prompt 调优

label：自由文本短标签（"snake_case-prefer" / "需 ci 后才能 review" 等），
       便于 group_by 与 prompt 模板 few-shot 注入选择。
"""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models._mixins import TimestampMixin, UUIDPKMixin


class EvalSample(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "eval_samples"

    issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("issues.id", ondelete="CASCADE"),
        nullable=False,
    )

    sample_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_eval_samples_issue_id", "issue_id"),
        Index("ix_eval_samples_sample_kind", "sample_kind"),
        Index("ix_eval_samples_label", "label"),
    )

    def __repr__(self) -> str:
        return f"<EvalSample {self.sample_kind}/{self.label} issue={self.issue_id}>"
