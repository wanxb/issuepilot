"""3.2 dev_tasks.scope_check JSONB column

Revision ID: h8j2k8f2h8i8
Revises: g7i1j7e1g7h7
Create Date: 2026-06-23 14:00:00.000000

Agent B 修改影响范围检查结果（scope_check 服务的输出），用于 Agent C
review prompt 注入 + 学习闭环统计 scope_creep 类失败。
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "h8j2k8f2h8i8"
down_revision: Union[str, None] = "g7i1j7e1g7h7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dev_tasks",
        sa.Column("scope_check", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dev_tasks", "scope_check")
