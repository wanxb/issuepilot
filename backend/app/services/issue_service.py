"""IssueService —— Issue 状态机的唯一变更入口。

允许的状态流转见 docs/DATA_MODEL.md §4。本类强制校验前置状态，
违规直接抛 InvalidTransitionError，永不静默改状态。
"""
from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import IssueStatus
from app.models.evaluation import Evaluation
from app.models.issue import Issue

log = structlog.get_logger(__name__)


# 状态流转白名单（key = 目标状态，value = 允许的前置状态集合）
_ALLOWED_FROM: dict[IssueStatus, set[IssueStatus]] = {
    IssueStatus.ANALYZING: {IssueStatus.DISCOVERED},
    IssueStatus.PENDING_DECISION: {IssueStatus.ANALYZING},
    IssueStatus.IGNORED: {IssueStatus.PENDING_DECISION},
    IssueStatus.QUEUED_DEV: {
        IssueStatus.PENDING_DECISION,
        IssueStatus.REVIEW_REJECTED,
        IssueStatus.DEV_FAILED,
        IssueStatus.PR_CLOSED,
    },
    IssueStatus.IN_DEV: {IssueStatus.QUEUED_DEV},
    IssueStatus.DEV_TESTING: {IssueStatus.IN_DEV},
    IssueStatus.DEV_FAILED: {IssueStatus.DEV_TESTING, IssueStatus.IN_DEV},
    IssueStatus.QUEUED_REVIEW: {IssueStatus.DEV_TESTING},
    IssueStatus.IN_REVIEW: {IssueStatus.QUEUED_REVIEW},
    IssueStatus.REVIEW_REJECTED: {IssueStatus.IN_REVIEW},
    IssueStatus.PR_SUBMITTED: {IssueStatus.IN_REVIEW},
    IssueStatus.PR_MERGED: {IssueStatus.PR_SUBMITTED},
    IssueStatus.PR_CLOSED: {IssueStatus.PR_SUBMITTED},
    IssueStatus.ARCHIVED: {
        IssueStatus.IGNORED,
        IssueStatus.PR_CLOSED,
        IssueStatus.PR_MERGED,
    },
}


class InvalidTransitionError(Exception):
    def __init__(self, *, from_state: IssueStatus, to_state: IssueStatus) -> None:
        super().__init__(
            f"invalid issue state transition: {from_state.value} → {to_state.value}",
        )
        self.from_state = from_state
        self.to_state = to_state


class IssueNotFoundError(Exception):
    pass


class InvalidDecisionError(Exception):
    """API 层 action 字段不识别（既不是 ignore 也不是 start_dev）。"""


class IssueService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ---- 查询 ----

    async def get(self, issue_id: uuid.UUID) -> Issue:
        stmt = (
            select(Issue)
            .where(Issue.id == issue_id)
            .options(selectinload(Issue.repository), selectinload(Issue.evaluation))
        )
        result = await self._s.execute(stmt)
        issue = result.scalar_one_or_none()
        if issue is None:
            raise IssueNotFoundError(f"issue {issue_id} not found")
        return issue

    # ---- 状态变更 ----

    async def transition(
        self,
        issue: Issue,
        *,
        to: IssueStatus,
    ) -> Issue:
        allowed_from = _ALLOWED_FROM.get(to)
        if allowed_from is None or issue.status not in allowed_from:
            raise InvalidTransitionError(from_state=issue.status, to_state=to)
        old = issue.status
        issue.status = to
        await self._s.flush()
        log.info(
            "issue.transition",
            issue_id=str(issue.id),
            from_state=old.value,
            to_state=to.value,
        )
        return issue

    async def mark_analyzing(self, issue: Issue) -> Issue:
        return await self.transition(issue, to=IssueStatus.ANALYZING)

    async def decide(
        self,
        issue: Issue,
        *,
        action: str,
    ) -> Issue:
        """用户决策（看板「忽略 / 加入开发」按钮）。

        action == "ignore":    PENDING_DECISION → IGNORED
        action == "start_dev": PENDING_DECISION → QUEUED_DEV

        不允许的状态前置 / 非法 action 都通过 transition() 的白名单或这里
        抛出 InvalidDecisionError，API 层映射 409 / 422。
        """
        if action == "ignore":
            return await self.transition(issue, to=IssueStatus.IGNORED)
        if action == "start_dev":
            return await self.transition(issue, to=IssueStatus.QUEUED_DEV)
        raise InvalidDecisionError(f"unknown action: {action!r}")

    async def mark_in_dev(self, issue: Issue) -> Issue:
        """QUEUED_DEV → IN_DEV"""
        return await self.transition(issue, to=IssueStatus.IN_DEV)

    async def mark_dev_testing(self, issue: Issue) -> Issue:
        """IN_DEV → DEV_TESTING"""
        return await self.transition(issue, to=IssueStatus.DEV_TESTING)

    async def mark_dev_failed(self, issue: Issue) -> Issue:
        """IN_DEV 或 DEV_TESTING → DEV_FAILED"""
        return await self.transition(issue, to=IssueStatus.DEV_FAILED)

    async def mark_queued_review(self, issue: Issue) -> Issue:
        """DEV_TESTING → QUEUED_REVIEW"""
        return await self.transition(issue, to=IssueStatus.QUEUED_REVIEW)

    async def finish_analyzing(
        self,
        issue: Issue,
        *,
        eval_payload: dict[str, Any],
    ) -> Evaluation:
        """评估完成：写 evaluations 行 + 状态转 PENDING_DECISION（事务原子）。"""
        if issue.status != IssueStatus.ANALYZING:
            raise InvalidTransitionError(
                from_state=issue.status,
                to_state=IssueStatus.PENDING_DECISION,
            )
        evaluation = Evaluation(issue_id=issue.id, **eval_payload)
        self._s.add(evaluation)
        await self._s.flush()
        await self.transition(issue, to=IssueStatus.PENDING_DECISION)
        return evaluation
