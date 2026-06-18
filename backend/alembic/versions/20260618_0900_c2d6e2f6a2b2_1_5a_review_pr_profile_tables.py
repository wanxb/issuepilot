"""1.5a review/pr/profile tables

Revision ID: c2d6e2f6a2b2
Revises: be207babb556
Create Date: 2026-06-18 09:00:00.000000

新建 5 张表，为 1.5b~e 提供数据基础：
    - repo_profiles       Agent D 仓库画像（1:1 repository，TTL 90d）
    - review_tasks        Agent C 评审记录
    - pull_requests       已提交 PR
    - pr_outcomes         PR 生命周期事件流水
    - rejection_reasons   退回 / 关闭 / revert 结构化原因（学习闭环核心）

注意 FK 创建顺序：repo_profiles → review_tasks → pull_requests
                 → pr_outcomes → rejection_reasons
downgrade 反序删表 + 显式 DROP TYPE（PG ENUM 不会随 drop_table 自动消失）。
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c2d6e2f6a2b2"
down_revision: Union[str, None] = "be207babb556"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Enum 取值（与 app/models/enums.py 严格一致）
# ---------------------------------------------------------------------------

_PROFILE_QUALITY = ("high", "medium", "low")
_REVIEW_TASK_STATUS = ("pending", "running", "completed", "failed")
_REVIEW_VERDICT = ("APPROVED", "REJECTED")
_PR_STATUS = ("OPEN", "MERGED", "CLOSED")
_PR_FINAL_OUTCOME = (
    "MERGED_CLEAN",
    "MERGED_WITH_CHANGES",
    "CLOSED_BY_MAINTAINER",
    "CLOSED_BY_US",
    "REVERTED",
    "STALE",
)
_PR_EVENT_TYPE = (
    "submitted",
    "review_received",
    "comment_received",
    "pushed_by_us",
    "pushed_by_maintainer",
    "merged",
    "closed",
    "reverted_detected",
)
_REJECTION_SOURCE = ("agent_c", "maintainer_review", "maintainer_close")
_REJECTION_CATEGORY = (
    "wrong_root_cause",
    "incomplete_fix",
    "broke_other_tests",
    "style_mismatch",
    "security_concern",
    "scope_creep",
    "needs_design_discussion",
    "duplicate",
    "out_of_scope",
    "other",
)
_REJECTION_SEVERITY = ("blocker", "major", "minor")
_REJECTION_DIMENSION = (
    "correctness",
    "test_coverage",
    "code_style",
    "security",
    "pr_description",
)
_AGENT_B_ATTRIBUTION = ("yes", "no", "unclear")


def upgrade() -> None:
    # ---- repo_profiles ----
    op.create_table(
        "repo_profiles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("repo_id", sa.UUID(), nullable=False),
        sa.Column("test_command", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("install_command", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("lint_command", sa.String(length=500), nullable=True),
        sa.Column("code_style_notes", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("contributing_summary", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("forbidden_patterns", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("pr_title_convention", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("merged_pr_examples", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "profile_quality",
            sa.Enum(*_PROFILE_QUALITY, name="profile_quality"),
            nullable=False,
            server_default="medium",
        ),
        sa.Column("quality_reason", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("forced_refresh_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.String(length=20), nullable=False, server_default="v1.0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("is_fallback", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_id", name="uq_repo_profiles_repo_id"),
    )
    op.create_index("ix_repo_profiles_expires_at", "repo_profiles", ["expires_at"])
    op.create_index(
        "ix_repo_profiles_profile_quality", "repo_profiles", ["profile_quality"]
    )

    # ---- review_tasks ----
    op.create_table(
        "review_tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("issue_id", sa.UUID(), nullable=False),
        sa.Column("dev_task_id", sa.UUID(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "status",
            sa.Enum(*_REVIEW_TASK_STATUS, name="review_task_status"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "verdict",
            sa.Enum(*_REVIEW_VERDICT, name="review_verdict"),
            nullable=True,
        ),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("dimensions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("pr_title", sa.String(length=500), nullable=True),
        sa.Column("pr_body", sa.Text(), nullable=True),
        sa.Column("overall_comment", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.String(length=100), nullable=True),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=20), nullable=False, server_default="v1.0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("is_fallback", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["issue_id"], ["issues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dev_task_id"], ["dev_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_review_tasks_issue_id", "review_tasks", ["issue_id"])
    op.create_index("ix_review_tasks_dev_task_id", "review_tasks", ["dev_task_id"])
    op.create_index("ix_review_tasks_status", "review_tasks", ["status"])

    # ---- pull_requests ----
    op.create_table(
        "pull_requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("issue_id", sa.UUID(), nullable=False),
        sa.Column("review_task_id", sa.UUID(), nullable=True),
        sa.Column("github_pr_number", sa.Integer(), nullable=False),
        sa.Column("github_pr_url", sa.String(length=500), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("head_repo", sa.String(length=255), nullable=False),
        sa.Column("head_branch", sa.String(length=200), nullable=False),
        sa.Column("base_repo", sa.String(length=255), nullable=False),
        sa.Column("base_branch", sa.String(length=200), nullable=False, server_default="main"),
        sa.Column(
            "status",
            sa.Enum(*_PR_STATUS, name="pull_request_status"),
            nullable=False,
            server_default="OPEN",
        ),
        sa.Column(
            "final_outcome",
            sa.Enum(*_PR_FINAL_OUTCOME, name="pr_final_outcome"),
            nullable=True,
        ),
        sa.Column("close_reason", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merger_login", sa.String(length=100), nullable=True),
        sa.Column("closer_login", sa.String(length=100), nullable=True),
        sa.Column("merge_commit_sha", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["issue_id"], ["issues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["review_task_id"], ["review_tasks.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("github_pr_url", name="uq_pull_requests_url"),
    )
    op.create_index("ix_pull_requests_issue_id", "pull_requests", ["issue_id"])
    op.create_index("ix_pull_requests_status", "pull_requests", ["status"])
    op.create_index(
        "ix_pull_requests_final_outcome", "pull_requests", ["final_outcome"]
    )

    # ---- pr_outcomes ----
    op.create_table(
        "pr_outcomes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("pr_id", sa.UUID(), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(*_PR_EVENT_TYPE, name="pr_outcome_event_type"),
            nullable=False,
        ),
        sa.Column("actor", sa.String(length=100), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["pr_id"], ["pull_requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pr_outcomes_pr_id", "pr_outcomes", ["pr_id"])
    op.create_index(
        "ix_pr_outcomes_pr_id_occurred_at", "pr_outcomes", ["pr_id", "occurred_at"]
    )
    op.create_index("ix_pr_outcomes_event_type", "pr_outcomes", ["event_type"])

    # ---- rejection_reasons ----
    op.create_table(
        "rejection_reasons",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("issue_id", sa.UUID(), nullable=False),
        sa.Column("pr_id", sa.UUID(), nullable=True),
        sa.Column("review_task_id", sa.UUID(), nullable=True),
        sa.Column(
            "source",
            sa.Enum(*_REJECTION_SOURCE, name="rejection_source"),
            nullable=False,
        ),
        sa.Column(
            "category",
            sa.Enum(*_REJECTION_CATEGORY, name="rejection_category"),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.Enum(*_REJECTION_SEVERITY, name="rejection_severity"),
            nullable=False,
        ),
        sa.Column(
            "dimension",
            sa.Enum(*_REJECTION_DIMENSION, name="rejection_dimension"),
            nullable=True,
        ),
        sa.Column(
            "agent_b_attribution",
            sa.Enum(*_AGENT_B_ATTRIBUTION, name="agent_b_attribution"),
            nullable=True,
        ),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("referenced_files", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("referenced_lines", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("classified_by", sa.String(length=50), nullable=True),
        sa.Column(
            "classifier_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["issue_id"], ["issues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pr_id"], ["pull_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["review_task_id"], ["review_tasks.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rejection_reasons_issue_id", "rejection_reasons", ["issue_id"])
    op.create_index("ix_rejection_reasons_pr_id", "rejection_reasons", ["pr_id"])
    op.create_index("ix_rejection_reasons_source", "rejection_reasons", ["source"])
    op.create_index("ix_rejection_reasons_category", "rejection_reasons", ["category"])
    op.create_index(
        "ix_rejection_reasons_attribution", "rejection_reasons", ["agent_b_attribution"]
    )


def downgrade() -> None:
    # 反序删除（rejection_reasons 引用最多）
    op.drop_index("ix_rejection_reasons_attribution", table_name="rejection_reasons")
    op.drop_index("ix_rejection_reasons_category", table_name="rejection_reasons")
    op.drop_index("ix_rejection_reasons_source", table_name="rejection_reasons")
    op.drop_index("ix_rejection_reasons_pr_id", table_name="rejection_reasons")
    op.drop_index("ix_rejection_reasons_issue_id", table_name="rejection_reasons")
    op.drop_table("rejection_reasons")

    op.drop_index("ix_pr_outcomes_event_type", table_name="pr_outcomes")
    op.drop_index("ix_pr_outcomes_pr_id_occurred_at", table_name="pr_outcomes")
    op.drop_index("ix_pr_outcomes_pr_id", table_name="pr_outcomes")
    op.drop_table("pr_outcomes")

    op.drop_index("ix_pull_requests_final_outcome", table_name="pull_requests")
    op.drop_index("ix_pull_requests_status", table_name="pull_requests")
    op.drop_index("ix_pull_requests_issue_id", table_name="pull_requests")
    op.drop_table("pull_requests")

    op.drop_index("ix_review_tasks_status", table_name="review_tasks")
    op.drop_index("ix_review_tasks_dev_task_id", table_name="review_tasks")
    op.drop_index("ix_review_tasks_issue_id", table_name="review_tasks")
    op.drop_table("review_tasks")

    op.drop_index("ix_repo_profiles_profile_quality", table_name="repo_profiles")
    op.drop_index("ix_repo_profiles_expires_at", table_name="repo_profiles")
    op.drop_table("repo_profiles")

    # PG ENUM 不会随 drop_table 自动删除
    for enum_name in (
        "agent_b_attribution",
        "rejection_dimension",
        "rejection_severity",
        "rejection_category",
        "rejection_source",
        "pr_outcome_event_type",
        "pr_final_outcome",
        "pull_request_status",
        "review_verdict",
        "review_task_status",
        "profile_quality",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
