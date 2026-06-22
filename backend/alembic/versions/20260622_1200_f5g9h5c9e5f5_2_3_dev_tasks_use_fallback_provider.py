"""2.3 dev_tasks.use_fallback_provider

Revision ID: f5g9h5c9e5f5
Revises: e4f8g4b8d4e4
Create Date: 2026-06-22 12:00:00.000000

Agent B 任务级 fallback：dev_worker / review_worker 在重入新 DevTask 时
按规则置 true，_setup_phase 据此切换沙箱内 Claude Code CLI 的 BASE_URL /
AUTH_TOKEN / MODEL 到 DeepSeek（models.yaml.agent_b.fallback 配置）。
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f5g9h5c9e5f5"
down_revision: Union[str, None] = "e4f8g4b8d4e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dev_tasks",
        sa.Column(
            "use_fallback_provider", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("dev_tasks", "use_fallback_provider")
