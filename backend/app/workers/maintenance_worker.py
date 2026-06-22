"""maintenance_worker — 定时维护任务（2.3）。

由 app/scheduler.py 中的 APScheduler 每天触发：
    - stale_archive_scan：IGNORED 状态 > 30 天 → ARCHIVED
    - revert_scan：MERGED PR 是否被 maintainer revert → 更新 final_outcome

设计取舍：
    - 触发器在 API 容器（APScheduler），执行在 worker 容器（Celery），
      与 dev/review/profile/classify 任务复用基础设施
    - revert 检测做 MVP：拉 merged_at 之后的提交，标题正则匹配 "Revert"
      + 引用 merge_commit_sha 或 PR #number。漏检 / 误检留给 3.x 强化
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from celery import Task
from sqlalchemy import select

from app.core.config import get_settings  # noqa: F401  导出方便后续读 token
from app.db.database import session_scope
from app.github.client import GitHubClient
from app.models.enums import (
    IssueStatus,
    PROutcomeEventType,
    PRFinalOutcome,
    PullRequestStatus,
)
from app.models.issue import Issue
from app.models.pr_outcome import PROutcome
from app.models.pull_request import PullRequest
from app.services.issue_service import (
    InvalidTransitionError,
    IssueNotFoundError,
    IssueService,
)
from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)


STALE_DAYS_DEFAULT = 30           # IGNORED 多久后自动 archive
REVERT_LOOKBACK_DAYS = 30         # 扫多久前的 merged PR
REVERT_TITLE_RE = re.compile(r"^revert\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# STALE 自动归档
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.workers.maintenance_worker.stale_archive_scan",
    queue="classify_queue",
    acks_late=True,
)
def stale_archive_scan(stale_days: int = STALE_DAYS_DEFAULT) -> dict[str, int]:
    """扫 IGNORED 状态停留超过 stale_days 的 issue → ARCHIVED。"""
    return asyncio.run(_stale_archive_scan_async(stale_days))


async def _stale_archive_scan_async(stale_days: int) -> dict[str, int]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
    archived = 0
    skipped = 0

    async with session_scope() as s:
        stmt = (
            select(Issue)
            .where(Issue.status == IssueStatus.IGNORED)
            .where(Issue.updated_at < cutoff)
        )
        rows = (await s.execute(stmt)).scalars().all()
        svc = IssueService(s)
        for issue in rows:
            try:
                await svc.mark_archived(
                    issue,
                    reason=f"stale_ignored_over_{stale_days}_days",
                )
                archived += 1
            except InvalidTransitionError as e:
                skipped += 1
                log.warning(
                    "stale_archive.transition_failed",
                    issue_id=str(issue.id), error=str(e),
                )

    log.info("stale_archive_scan.done",
             stale_days=stale_days, archived=archived, skipped=skipped)
    return {"archived": archived, "skipped": skipped, "stale_days": stale_days}


# ---------------------------------------------------------------------------
# Revert 巡检
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.workers.maintenance_worker.revert_scan",
    queue="classify_queue",
    acks_late=True,
)
def revert_scan(lookback_days: int = REVERT_LOOKBACK_DAYS) -> dict[str, int]:
    return asyncio.run(_revert_scan_async(lookback_days))


async def _revert_scan_async(lookback_days: int) -> dict[str, int]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    settings = get_settings()
    token = (
        settings.github_token.get_secret_value() if settings.github_token else None
    )

    checked = 0
    reverted_found = 0

    async with session_scope() as s:
        stmt = (
            select(PullRequest)
            .where(PullRequest.status == PullRequestStatus.MERGED)
            .where(PullRequest.final_outcome != PRFinalOutcome.REVERTED)
            .where(PullRequest.merged_at >= cutoff)
        )
        prs = list((await s.execute(stmt)).scalars().all())

    # GitHub API 调用不持 DB 锁
    revert_events: list[tuple[uuid.UUID, str, str]] = []  # (pr_id, sha, message)
    async with GitHubClient(token=token) as gh:
        for pr in prs:
            checked += 1
            sha = pr.merge_commit_sha
            if not sha or not pr.merged_at or not pr.base_repo:
                continue
            try:
                revert_sha, revert_msg = await _find_revert(
                    gh, pr.base_repo, since=pr.merged_at,
                    merge_sha=sha, pr_number=pr.github_pr_number,
                )
            except Exception as e:  # GitHub API hiccup 不影响其他 PR
                log.warning(
                    "revert_scan.api_error",
                    pr_id=str(pr.id), error=str(e)[:200],
                )
                continue
            if revert_sha is not None:
                revert_events.append((pr.id, revert_sha, revert_msg))
                reverted_found += 1

    # 持久化
    if revert_events:
        async with session_scope() as s:
            svc = IssueService(s)
            for pr_id, revert_sha, msg in revert_events:
                pr = await s.get(PullRequest, pr_id)
                if pr is None:
                    continue
                pr.final_outcome = PRFinalOutcome.REVERTED
                s.add(PROutcome(
                    pr_id=pr.id,
                    event_type=PROutcomeEventType.REVERTED_DETECTED,
                    actor=None,
                    payload={"revert_sha": revert_sha, "message": msg[:1000]},
                    occurred_at=datetime.now(timezone.utc),
                ))
                # Issue 不再做状态转换（PR_MERGED 是终态）；revert 只标记 PR
                try:
                    await s.refresh(pr, ["issue"])
                    log.info(
                        "revert_scan.detected",
                        pr_id=str(pr.id),
                        issue_id=str(pr.issue_id),
                        revert_sha=revert_sha,
                    )
                except IssueNotFoundError:
                    pass

    log.info("revert_scan.done",
             checked=checked, reverted=reverted_found, lookback_days=lookback_days)
    return {"checked": checked, "reverted": reverted_found,
            "lookback_days": lookback_days}


async def _find_revert(
    gh: GitHubClient, base_repo: str, *,
    since: datetime, merge_sha: str, pr_number: int,
) -> tuple[str | None, str]:
    """返回 (revert_commit_sha, full_message) 或 (None, '')。

    策略：GET /repos/{repo}/commits?since=ISO 拉提交，找标题以 Revert 开头
    且 body 引用 merge_sha 或 #pr_number 的。limit 100，跨 100+ commit
    的 revert 漏检留给 3.x。
    """
    short_sha = (merge_sha or "")[:7]
    pr_ref = f"#{pr_number}"
    iso = since.isoformat().replace("+00:00", "Z")
    # GitHubClient 用 _get；list_commits 还没单独封；直接调 _get
    owner, name = base_repo.split("/", 1)
    commits = await gh._get(
        f"/repos/{owner}/{name}/commits",
        params={"since": iso, "per_page": 100},
    )

    for c in commits or []:
        msg = (c.get("commit") or {}).get("message", "")
        title = msg.split("\n", 1)[0]
        if not REVERT_TITLE_RE.match(title):
            continue
        # 必须引用原 merge SHA 或 PR 号才算真正的 revert
        if short_sha and short_sha in msg:
            return c.get("sha"), msg
        if pr_ref and pr_ref in msg:
            return c.get("sha"), msg
    return None, ""


# ---------------------------------------------------------------------------
# 手动触发入口（管理员 / 端到端验证用）
# ---------------------------------------------------------------------------


def trigger(name: str, **kwargs: object) -> str:
    """同步触发一个维护任务到 classify_queue，返回 task_id。

    name: "stale_archive_scan" | "revert_scan"
    """
    task_name = f"app.workers.maintenance_worker.{name}"
    result = celery_app.send_task(
        task_name, kwargs=kwargs, queue="classify_queue",
    )
    return result.id
