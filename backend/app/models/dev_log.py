"""DevLog（Agent B 实时执行日志，逐条入库 + WebSocket 推送）。

写入策略：
    - Agent B 沙箱 stdout 解析后逐 chunk 写
    - 系统事件（启动 / 超时 / 杀死容器）也以 INFO/ERROR 级写
    - level / step 用 enum 便于前端着色与筛选
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models._mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import DevLogLevel, DevLogStep

if TYPE_CHECKING:
    from app.models.dev_task import DevTask


class DevLog(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "dev_logs"

    dev_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dev_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )

    level: Mapped[DevLogLevel] = mapped_column(
        Enum(DevLogLevel, name="dev_log_level", native_enum=True),
        nullable=False,
        default=DevLogLevel.INFO,
    )
    step: Mapped[DevLogStep] = mapped_column(
        Enum(DevLogStep, name="dev_log_step", native_enum=True),
        nullable=False,
        default=DevLogStep.SYSTEM,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)

    dev_task: Mapped["DevTask"] = relationship(back_populates="logs")

    __table_args__ = (
        Index("ix_dev_logs_dev_task_id", "dev_task_id"),
        Index("ix_dev_logs_dev_task_id_created_at", "dev_task_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<DevLog {self.step.value}/{self.level.value} {self.message[:40]}>"
