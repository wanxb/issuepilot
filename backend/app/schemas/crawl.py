"""POST /crawl-jobs/manual 的请求 / 响应 schema。"""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

ManualMode = Literal["repo", "issue"]


class ManualSubmitRequest(BaseModel):
    url: str = Field(min_length=1, description="GitHub repo or issue URL")
    max_issues: int = Field(default=50, ge=1, le=200)
    force_confirm: bool = Field(
        default=False,
        description=(
            "When repo open_count > max_issues, the server first returns "
            "needs_confirmation=true. Pass force_confirm=true to actually enqueue."
        ),
    )


class ManualSubmitResponse(BaseModel):
    job_id: uuid.UUID
    mode: ManualMode
    repo_full_name: str
    issues_enqueued: int = 0
    issues_reused: int = 0
    issues_skipped_reason: dict[str, int] = Field(default_factory=dict)
    needs_confirmation: bool = False
    open_count_total: int | None = None
