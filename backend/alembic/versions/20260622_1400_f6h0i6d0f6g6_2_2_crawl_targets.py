"""2.2 crawl_targets

Revision ID: f6h0i6d0f6g6
Revises: f5g9h5c9e5f5
Create Date: 2026-06-22 14:00:00.000000

定时抓取配置表。scheduler 启动时载入所有 enabled=true 的行，按 cron 注册
APScheduler job。一条 = 一个独立任务（GitHub Trending / search / explicit
repos）。
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "f6h0i6d0f6g6"
down_revision: Union[str, None] = "f5g9h5c9e5f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "crawl_targets",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("spec", JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("cron", sa.String(50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(20), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_crawl_targets_enabled", "crawl_targets", ["enabled"])
    op.create_index("ix_crawl_targets_source", "crawl_targets", ["source"])


def downgrade() -> None:
    op.drop_index("ix_crawl_targets_source", table_name="crawl_targets")
    op.drop_index("ix_crawl_targets_enabled", table_name="crawl_targets")
    op.drop_table("crawl_targets")
