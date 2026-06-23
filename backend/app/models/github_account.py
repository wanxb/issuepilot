"""GitHub 账号（3.3 多账号支持）。

每条 = 一个 GitHub token（可能是 PAT、fine-grained PAT 或 GitHub App
installation token）。CrawlerService 用 role=crawler 的，PRService 用
role=dev 的。

token 字段加密放到数据库吗？暂存明文（与现有 .env 暴露同等敏感度），
后续可在 Settings 层做封装。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models._mixins import TimestampMixin, UUIDPKMixin


class GitHubAccount(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "github_accounts"

    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    # 实际 token；明文存（同 .env 等级敏感度）
    token: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true",
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        Index("ix_github_accounts_role", "role"),
        Index("ix_github_accounts_enabled", "enabled"),
    )

    def __repr__(self) -> str:
        return f"<GitHubAccount {self.name}({self.role}) en={self.enabled}>"
