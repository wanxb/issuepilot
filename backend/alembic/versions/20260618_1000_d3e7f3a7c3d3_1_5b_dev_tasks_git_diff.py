"""1.5b dev_tasks git_diff and base_sha

Revision ID: d3e7f3a7c3d3
Revises: c2d6e2f6a2b2
Create Date: 2026-06-18 10:00:00.000000

为 Agent C 评审采集做准备：
    - dev_tasks.git_diff   TEXT NULL   完整 git diff（沙箱内 git diff base..HEAD 产出）
    - dev_tasks.base_sha   VARCHAR(64) clone 时记录的 base SHA
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d3e7f3a7c3d3"
down_revision: Union[str, None] = "c2d6e2f6a2b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dev_tasks",
        sa.Column("git_diff", sa.Text(), nullable=True),
    )
    op.add_column(
        "dev_tasks",
        sa.Column("base_sha", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dev_tasks", "base_sha")
    op.drop_column("dev_tasks", "git_diff")
