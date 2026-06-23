"""Blacklist（黑名单）— 3.3。

entity_type：
    - repo    pattern = "owner/repo" 或 "owner/*"（glob 通配 1 级）
    - issue   pattern = "owner/repo#42"

CrawlerService 在 upsert repo / issue 前调 `is_blacklisted` 检查；命中则
跳过且记 stats，避免把已知有毒 / 重复 / out-of-scope 的内容反复评估。
"""
from __future__ import annotations

from sqlalchemy import Boolean, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models._mixins import TimestampMixin, UUIDPKMixin


class Blacklist(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "blacklist"

    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    pattern: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true",
    )

    __table_args__ = (
        Index("ix_blacklist_entity_type", "entity_type"),
        Index("ix_blacklist_pattern", "pattern"),
        Index("ix_blacklist_enabled", "enabled"),
    )

    def __repr__(self) -> str:
        return f"<Blacklist {self.entity_type}={self.pattern} en={self.enabled}>"
