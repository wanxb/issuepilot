"""3.3 github_accounts

Revision ID: j0l4m0h4j0k0
Revises: i9k3l9g3i9j9
Create Date: 2026-06-23 18:00:00.000000

多 GitHub 账号 token 池（crawler / dev / webhook 等角色分账号）。
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "j0l4m0h4j0k0"
down_revision: Union[str, None] = "i9k3l9g3i9j9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "github_accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("username", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_github_accounts_role", "github_accounts", ["role"])
    op.create_index("ix_github_accounts_enabled", "github_accounts", ["enabled"])


def downgrade() -> None:
    op.drop_index("ix_github_accounts_enabled", table_name="github_accounts")
    op.drop_index("ix_github_accounts_role", table_name="github_accounts")
    op.drop_table("github_accounts")
