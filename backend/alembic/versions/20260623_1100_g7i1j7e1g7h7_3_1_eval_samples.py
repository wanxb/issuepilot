"""3.1 eval_samples

Revision ID: g7i1j7e1g7h7
Revises: f6h0i6d0f6g6
Create Date: 2026-06-23 11:00:00.000000

Prompt 质量提升的 Golden Set 标注表。
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "g7i1j7e1g7h7"
down_revision: Union[str, None] = "f6h0i6d0f6g6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "eval_samples",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("issue_id", UUID(as_uuid=True),
                  sa.ForeignKey("issues.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("sample_kind", sa.String(40), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("source", sa.String(40), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_eval_samples_issue_id", "eval_samples", ["issue_id"])
    op.create_index("ix_eval_samples_sample_kind", "eval_samples", ["sample_kind"])
    op.create_index("ix_eval_samples_label", "eval_samples", ["label"])


def downgrade() -> None:
    op.drop_index("ix_eval_samples_label", table_name="eval_samples")
    op.drop_index("ix_eval_samples_sample_kind", table_name="eval_samples")
    op.drop_index("ix_eval_samples_issue_id", table_name="eval_samples")
    op.drop_table("eval_samples")
