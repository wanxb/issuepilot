"""DevTask（Agent B 单次开发尝试）。

每次"用户决策开发 → Agent B 处理"对应一行；同一 Issue 失败重入会有多行
（attempt_number 自增）。

containers / 分支 / fork 元数据用于审计 + WebSocket 推送上下文。
test_result JSONB 与 AGENT_DESIGN.md §Agent B 输出 schema 对齐。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models._mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import DevTaskStatus

if TYPE_CHECKING:
    from app.models.dev_log import DevLog
    from app.models.issue import Issue


class DevTask(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "dev_tasks"

    issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("issues.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    status: Mapped[DevTaskStatus] = mapped_column(
        Enum(DevTaskStatus, name="dev_task_status", native_enum=True),
        nullable=False,
        default=DevTaskStatus.PENDING,
    )

    # 沙箱 / GitHub 上下文
    container_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sandbox_image: Mapped[str | None] = mapped_column(String(100), nullable=True)
    forked_repo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    workspace_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # 评审退回时携带的意见（Agent C → Agent B 重入循环）
    review_context: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 执行结果（report_completion / report_failure 写入）
    files_changed: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    diff_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 1.5b: 从沙箱采集的完整 git diff，供 Agent C 评审使用（≤200KB，超出截断）
    git_diff: Mapped[str | None] = mapped_column(Text, nullable=True)
    # base SHA：clone 时记录，便于 PR 创建 / revert 检测时锁定起点
    base_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 1.5d: 沙箱内 git push 的结果（True 成功 / False 失败 / None 未尝试）
    branch_pushed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # 2.3: 任务级 fallback——重试 DevTask 时切到 models.yaml.agent_b.fallback
    # 配置（DeepSeek 的 Anthropic 兼容端点）
    use_fallback_provider: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
    )
    failure_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 测试结果（与 AgentBOutput 对齐）
    test_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # 3.2: 修改影响范围检查结果（scope_check.py 输出）
    scope_check: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Loop 元数据
    loop_iterations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stuck_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cost_usd: Mapped[float] = mapped_column(default=0.0, nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # relationships
    issue: Mapped["Issue"] = relationship(lazy="select")
    logs: Mapped[list["DevLog"]] = relationship(
        back_populates="dev_task",
        cascade="all, delete-orphan",
        order_by="DevLog.created_at",
    )

    __table_args__ = (
        Index("ix_dev_tasks_issue_id", "issue_id"),
        Index("ix_dev_tasks_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<DevTask issue={self.issue_id} attempt={self.attempt_number} {self.status.value}>"
