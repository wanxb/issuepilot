"""GET /api/v1/issues 响应 schema。"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    IssueDifficulty,
    IssueSource,
    IssueStatus,
    PRFinalOutcome,
    PullRequestStatus,
)


class RepoView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    full_name: str
    primary_language: str | None = None
    stars: int = 0


class EvaluationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    total_score: float
    difficulty: IssueDifficulty
    estimated_hours: float
    summary: str
    is_worth_developing: bool
    is_fallback: bool = False


class PullRequestView(BaseModel):
    """看板 PR 卡片需要的字段（1.5e）。"""

    model_config = ConfigDict(from_attributes=True)
    github_pr_number: int
    github_pr_url: str
    status: PullRequestStatus
    final_outcome: PRFinalOutcome | None = None
    title: str
    submitted_at: datetime


class IssueListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: IssueStatus
    source: IssueSource
    github_url: str
    title: str
    created_at: datetime
    repository: RepoView
    evaluation: EvaluationView | None = None
    active_dev_task_id: uuid.UUID | None = None
    pull_request: PullRequestView | None = None


class IssueListResponse(BaseModel):
    items: list[IssueListItem]
    page: int = 1
    page_size: int = 20
    total: int = 0


class IssueListFilters(BaseModel):
    status: list[IssueStatus] | None = None
    min_score: float | None = Field(default=None, ge=0.0, le=10.0)
    language: str | None = None
    repo: str | None = None
    source: IssueSource | None = None
    sort: str = Field(default="-score")  # 见 API_SPEC 默认 score desc
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
