"""PRTracker —— GitHub Webhook 事件 → pr_outcomes / pull_requests 持久化。

只关心三类事件：
    - pull_request (action=closed)              → on_pr_closed
    - pull_request_review (action=submitted)    → on_review_received
    - issue_comment (action=created)            → on_comment_received

不做语义分类（rejection 推断留给 2.3 RejectionClassifier Agent）。
本 service 是 webhook handler 的薄持久化层。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    AgentBAttribution,
    IssueStatus,
    PROutcomeEventType,
    PRFinalOutcome,
    PullRequestStatus,
    RejectionCategory,
    RejectionSeverity,
    RejectionSource,
)
from app.models.pr_outcome import PROutcome
from app.models.pull_request import PullRequest
from app.models.rejection_reason import RejectionReason
from app.services.issue_service import (
    InvalidTransitionError,
    IssueService,
)

log = structlog.get_logger(__name__)


class PRNotTracked(Exception):
    """webhook 提到的 PR 不是本系统创建的（pull_requests 表里没有）。"""


class PRTracker:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ---- 查找 ----

    async def find_by_url(self, github_pr_url: str) -> PullRequest:
        stmt = select(PullRequest).where(PullRequest.github_pr_url == github_pr_url)
        result = await self._s.execute(stmt)
        pr = result.scalar_one_or_none()
        if pr is None:
            raise PRNotTracked(f"PR not tracked: {github_pr_url}")
        return pr

    # ---- 事件入库（通用）----

    async def record_event(
        self,
        pr: PullRequest,
        *,
        event_type: PROutcomeEventType,
        actor: str | None,
        payload: dict[str, Any] | None,
        occurred_at: datetime,
    ) -> PROutcome:
        outcome = PROutcome(
            pr_id=pr.id,
            event_type=event_type,
            actor=actor,
            payload=payload,
            occurred_at=occurred_at,
        )
        self._s.add(outcome)
        await self._s.flush()
        return outcome

    # ---- 具体事件处理 ----

    async def on_pr_closed(
        self,
        pr: PullRequest,
        *,
        merged: bool,
        actor: str | None,
        merge_commit_sha: str | None,
        occurred_at: datetime,
        raw_payload: dict[str, Any] | None = None,
    ) -> None:
        """PR closed 事件。

        merged=True  → PullRequest.status=MERGED, final_outcome=MERGED_CLEAN,
                       Issue → PR_MERGED
        merged=False → PullRequest.status=CLOSED, final_outcome=CLOSED_BY_MAINTAINER,
                       Issue → PR_CLOSED

        细化的 final_outcome（MERGED_WITH_CHANGES / REVERTED / STALE / CLOSED_BY_US）
        交给 2.3 / 3.x 阶段补。
        """
        await self.record_event(
            pr,
            event_type=PROutcomeEventType.MERGED if merged else PROutcomeEventType.CLOSED,
            actor=actor,
            payload=_compact_payload(raw_payload, keys=("merged", "merge_commit_sha", "closed_at")),
            occurred_at=occurred_at,
        )

        if merged:
            pr.status = PullRequestStatus.MERGED
            pr.merged_at = occurred_at
            pr.merger_login = actor
            pr.merge_commit_sha = merge_commit_sha
            pr.final_outcome = PRFinalOutcome.MERGED_CLEAN
        else:
            pr.status = PullRequestStatus.CLOSED
            pr.closed_at = occurred_at
            pr.closer_login = actor
            pr.final_outcome = PRFinalOutcome.CLOSED_BY_MAINTAINER
            # 2.3: maintainer 关 PR（非 merge）→ 强信号，必写一条
            self._add_unclassified_rejection(
                pr=pr,
                source=RejectionSource.MAINTAINER_CLOSE,
                raw_text=None,
                detail=f"PR closed by {actor or 'maintainer'} without merge",
            )

        # Issue 状态转换
        await self._s.refresh(pr, ["issue"])
        issue = pr.issue
        svc = IssueService(self._s)
        try:
            if merged:
                if issue.status == IssueStatus.PR_SUBMITTED:
                    await svc.mark_pr_merged(issue)
            else:
                if issue.status == IssueStatus.PR_SUBMITTED:
                    await svc.mark_pr_closed(issue)
        except InvalidTransitionError as e:
            log.error(
                "pr_tracker.transition_failed",
                pr_id=str(pr.id),
                merged=merged,
                error=str(e),
            )

    async def on_review_received(
        self,
        pr: PullRequest,
        *,
        reviewer: str | None,
        state: str,        # "approved" / "changes_requested" / "commented"
        body: str | None,
        occurred_at: datetime,
    ) -> None:
        await self.record_event(
            pr,
            event_type=PROutcomeEventType.REVIEW_RECEIVED,
            actor=reviewer,
            payload={"state": state, "body": (body or "")[:5000]},
            occurred_at=occurred_at,
        )
        # 2.3: 非 approved review → 写 rejection_reasons 占位（classifier 二次分类）
        if state != "approved" and (body or "").strip():
            self._add_unclassified_rejection(
                pr=pr,
                source=RejectionSource.MAINTAINER_REVIEW,
                raw_text=body,
                detail=f"[review state={state}] {body[:1500]}",
            )

    async def on_comment_received(
        self,
        pr: PullRequest,
        *,
        commenter: str | None,
        body: str | None,
        occurred_at: datetime,
    ) -> None:
        await self.record_event(
            pr,
            event_type=PROutcomeEventType.COMMENT_RECEIVED,
            actor=commenter,
            payload={"body": (body or "")[:5000]},
            occurred_at=occurred_at,
        )
        # 2.3: PR comment → 写 rejection_reasons 占位（classifier 决定语义）
        # 注：自己 bot 留的评论也会被写入；filter 留给 classifier。
        if (body or "").strip():
            self._add_unclassified_rejection(
                pr=pr,
                source=RejectionSource.MAINTAINER_REVIEW,
                raw_text=body,
                detail=f"[pr_comment] {body[:1500]}",
            )


    # ---- rejection_reasons 占位写入（classified_by=None；2.3 RejectionClassifier 二次分类）----

    def _add_unclassified_rejection(
        self,
        *,
        pr: PullRequest,
        source: RejectionSource,
        raw_text: str | None,
        detail: str,
    ) -> None:
        """写一条 classifier 未跑过的占位记录。

        category/severity/dimension 用保守默认；分类器跑完会 UPDATE 这一行。
        attribution=UNCLEAR 直到分类器判断责任在 Agent B 还是 maintainer。
        """
        reason = RejectionReason(
            issue_id=pr.issue_id,
            pr_id=pr.id,
            review_task_id=None,        # maintainer 路径不对应特定 review_task
            source=source,
            category=RejectionCategory.OTHER,
            severity=RejectionSeverity.MINOR,
            dimension=None,
            agent_b_attribution=AgentBAttribution.UNCLEAR,
            detail=detail[:2000],
            raw_text=raw_text,
            classified_by=None,         # ← 待 RejectionClassifier 处理的信号
        )
        self._s.add(reason)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _compact_payload(
    raw: dict[str, Any] | None,
    *,
    keys: tuple[str, ...],
) -> dict[str, Any] | None:
    if not raw:
        return None
    return {k: raw.get(k) for k in keys if k in raw}


def _parse_dt(s: object) -> datetime:
    if isinstance(s, str):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)
