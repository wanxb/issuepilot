"""GitHub API 响应的 Pydantic 视图。

只暴露我们用到的字段，避免对 GitHub 返回结构强耦合。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class GHUser(BaseModel):
    login: str
    type: str | None = None


class GHRepo(BaseModel):
    id: int
    name: str
    full_name: str
    owner: GHUser
    description: str | None = None
    language: str | None = None
    topics: list[str] = Field(default_factory=list)
    stargazers_count: int = 0
    forks_count: int = 0
    open_issues_count: int = 0
    pushed_at: datetime | None = None
    private: bool = False
    fork: bool = False
    archived: bool = False
    disabled: bool = False
    default_branch: str = "main"


class GHIssue(BaseModel):
    id: int
    number: int
    title: str
    body: str | None = None
    state: str
    html_url: str
    user: GHUser | None = None
    labels: list[Any] = Field(default_factory=list)
    pull_request: dict[str, Any] | None = None  # GH 把 PR 也算 issue，这里非 None 时跳过
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None

    @property
    def label_names(self) -> list[str]:
        names: list[str] = []
        for raw in self.labels:
            if isinstance(raw, dict) and isinstance(raw.get("name"), str):
                names.append(raw["name"])
            elif isinstance(raw, str):
                names.append(raw)
        return names

    @property
    def is_pull_request(self) -> bool:
        return self.pull_request is not None


class GHPullRequest(BaseModel):
    id: int
    number: int
    title: str
    state: str
    html_url: str
    merged_at: datetime | None = None
    closed_at: datetime | None = None
    user: GHUser | None = None


class RateLimit(BaseModel):
    limit: int = 0
    remaining: int = 0
    reset: datetime | None = None

    @classmethod
    def from_headers(cls, headers: dict[str, str] | Any) -> "RateLimit":
        try:
            limit = int(headers.get("X-RateLimit-Limit", "0"))
        except (TypeError, ValueError):
            limit = 0
        try:
            remaining = int(headers.get("X-RateLimit-Remaining", "0"))
        except (TypeError, ValueError):
            remaining = 0
        reset: datetime | None = None
        reset_raw = headers.get("X-RateLimit-Reset")
        if reset_raw:
            try:
                from datetime import UTC

                reset = datetime.fromtimestamp(int(reset_raw), tz=UTC)
            except (TypeError, ValueError):
                reset = None
        return cls(limit=limit, remaining=remaining, reset=reset)
