"""Agent 输入 / 输出 schema（Pydantic）。

严格对齐 docs/AGENT_DESIGN.md，字段名、约束、字典 key 都不可改。
"""
from __future__ import annotations

from typing import Any, Literal

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
# Agent C
# ---------------------------------------------------------------------------


class TestResult(BaseModel):
    """与 AgentBOutput 的 test_* 字段对齐，传给 Agent C 评审。"""

    # pytest 默认按 Test* 前缀收集类，本 Pydantic 模型不是测试类
    __test__ = False

    test_passed: bool
    total_tests: int = 0
    failed_tests: int = 0
    new_tests_added: int = 0
    test_output_snippet: str = ""


class AgentCInput(BaseModel):
    issue_title: str
    issue_body: str
    repo_full_name: str
    repo_language: str | None = None
    diff_content: str                # git diff（必要时由 caller 截断）
    diff_summary: str                # Agent B 的 200 字摘要
    test_result: TestResult
    files_changed: list[str] = Field(default_factory=list)
    attempt_number: int = 1
    previous_rejections: list[str] = Field(default_factory=list)
    # 1.5c 注入：repo_profile.code_style_notes（空串表示无 profile）
    repo_style_notes: str = ""
    repo_contributing_summary: str = ""


class ReviewDimension(BaseModel):
    score: int = Field(ge=1, le=10)
    passed: bool
    comment: str

    @field_validator("comment")
    @classmethod
    def comment_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("comment must not be empty")
        return v


_REVIEW_DIMENSIONS = (
    "correctness",
    "test_coverage",
    "code_style",
    "security",
    "pr_description",
)


class AgentCOutput(BaseModel):
    verdict: Literal["APPROVED", "REJECTED"]
    overall_score: float = Field(ge=0.0, le=10.0)
    dimensions: dict[str, ReviewDimension]
    rejection_reason: str | None = None
    pr_title: str | None = None
    pr_body: str | None = None
    overall_comment: str

    @field_validator("dimensions")
    @classmethod
    def all_dimensions_present(cls, v: dict[str, ReviewDimension]) -> dict[str, ReviewDimension]:
        missing = [k for k in _REVIEW_DIMENSIONS if k not in v]
        if missing:
            raise ValueError(f"missing required review dimensions: {missing}")
        return v

    @field_validator("rejection_reason")
    @classmethod
    def reason_when_rejected(cls, v: str | None, info: Any) -> str | None:  # type: ignore[override]
        # pydantic v2 ValidationInfo — 用 info.data 读已校验字段
        verdict = info.data.get("verdict")
        if verdict == "REJECTED":
            if not v or not v.strip():
                raise ValueError("rejection_reason is required when verdict=REJECTED")
        return v

    @field_validator("pr_title")
    @classmethod
    def title_when_approved(cls, v: str | None, info: Any) -> str | None:  # type: ignore[override]
        verdict = info.data.get("verdict")
        if verdict == "APPROVED":
            if not v or not v.strip():
                raise ValueError("pr_title is required when verdict=APPROVED")
        return v

    @field_validator("pr_body")
    @classmethod
    def body_when_approved(cls, v: str | None, info: Any) -> str | None:  # type: ignore[override]
        verdict = info.data.get("verdict")
        if verdict == "APPROVED":
            if not v or not v.strip():
                raise ValueError("pr_body is required when verdict=APPROVED")
        return v
