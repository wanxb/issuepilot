"""3.3 blacklist

Revision ID: i9k3l9g3i9j9
Revises: h8j2k8f2h8i8
Create Date: 2026-06-23 16:00:00.000000

Issue / repo 黑名单。Crawler 在 upsert 前查表跳过。
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "i9k3l9g3i9j9"
down_revision: Union[str, None] = "h8j2k8f2h8i8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "blacklist",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("pattern", sa.String(200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_blacklist_entity_type", "blacklist", ["entity_type"])
    op.create_index("ix_blacklist_pattern", "blacklist", ["pattern"])
    op.create_index("ix_blacklist_enabled", "blacklist", ["enabled"])


def downgrade() -> None:
    op.drop_index("ix_blacklist_enabled", table_name="blacklist")
    op.drop_index("ix_blacklist_pattern", table_name="blacklist")
    op.drop_index("ix_blacklist_entity_type", table_name="blacklist")
    op.drop_table("blacklist")
