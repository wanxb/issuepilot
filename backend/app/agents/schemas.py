"""Agent 输入 / 输出 schema（Pydantic）。

严格对齐 docs/AGENT_DESIGN.md，字段名、约束、字典 key 都不可改。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Agent A
# ---------------------------------------------------------------------------


class AgentAInput(BaseModel):
    issue_title: str
    issue_body: str
    issue_labels: list[str] = Field(default_factory=list)
    issue_url: str
    repo_full_name: str
    repo_description: str | None = None
    repo_language: str | None = None
    repo_stars: int = 0
    repo_topics: list[str] = Field(default_factory=list)
    repo_last_commit_days: int = 0   # 距今天数；越大越不活跃
    repo_open_prs_count: int = 0
    repo_merged_prs_last_30d: int = 0


class Dimension(BaseModel):
    score: float = Field(ge=0.0, le=10.0)
    comment: str

    @field_validator("comment")
    @classmethod
    def comment_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("comment must not be empty")
        return v


_REQUIRED_DIMENSIONS = (
    "clarity",
    "feasibility",
    "value",
    "repo_activity",
    "context_sufficiency",
)


class AgentAOutput(BaseModel):
    total_score: float = Field(ge=0.0, le=10.0)
    difficulty: Literal["easy", "medium", "hard"]
    estimated_hours: float = Field(ge=0.0)
    summary: str
    recommendation: str
    is_worth_developing: bool
    dimensions: dict[str, Dimension]

    @field_validator("dimensions")
    @classmethod
    def all_dimensions_present(cls, v: dict[str, Dimension]) -> dict[str, Dimension]:
        missing = [k for k in _REQUIRED_DIMENSIONS if k not in v]
        if missing:
            raise ValueError(f"missing required dimensions: {missing}")
        return v


# ---------------------------------------------------------------------------
# Agent B
# ---------------------------------------------------------------------------


class AgentBInput(BaseModel):
    issue_title: str
    issue_body: str
    issue_url: str
    repo_full_name: str
    repo_language: str | None = None
    evaluation_summary: str
    review_context: str | None = None
    attempt_number: int = 1
    branch_name: str
    forked_repo: str


class AgentBOutput(BaseModel):
    success: bool
    files_changed: list[str]
    diff_summary: str
    test_passed: bool
    total_tests: int
    failed_tests: int
    new_tests_added: int
    test_output_snippet: str


class AgentBFailure(BaseModel):
    reason: Literal[
        "cannot_locate_issue",
        "requires_external_deps",
        "issue_is_invalid",
        "test_infrastructure",
        "out_of_scope",
    ]
    detail: str


# ---------------------------------------------------------------------------
# 后续 Agent（C / D）放这里，1.5+ 实现
# ---------------------------------------------------------------------------
