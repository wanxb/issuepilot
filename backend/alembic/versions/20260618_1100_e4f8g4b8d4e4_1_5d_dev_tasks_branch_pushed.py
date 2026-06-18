"""1.5d dev_tasks branch_pushed

Revision ID: e4f8g4b8d4e4
Revises: d3e7f3a7c3d3
Create Date: 2026-06-18 11:00:00.000000

PRService 在 review_worker APPROVED 路径创建 PR 前需要知道沙箱内
git push 是否成功；本列由 dev_worker 在沙箱采集时填写。
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e4f8g4b8d4e4"
down_revision: Union[str, None] = "d3e7f3a7c3d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dev_tasks",
        sa.Column("branch_pushed", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dev_tasks", "branch_pushed")
