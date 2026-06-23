"""RepoProfile 智能刷新（3.2）。

策略：当某 repo 近 N 天内有 ≥ THRESHOLD 条 `style_mismatch + agent_b_attribution=yes`
的 rejection_reasons 时，认为当前 profile 描述的代码规范不准确，触发强制
重新生成 RepoProfile（设 expires_at = now + 立即入 profile_queue）。

调用入口：
    - classify_worker 写完分类后调 `maybe_force_refresh(session, issue_id)`
    - admin API 可手动 trigger 或诊断
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    AgentBAttribution,
    RejectionCategory,
)
from app.models.issue import Issue
from app.models.rejection_reason import RejectionReason
from app.models.repo_profile import RepoProfile

log = structlog.get_logger(__name__)


DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_THRESHOLD = 3


async def count_style_rejections_for_repo(
    session: AsyncSession,
    repo_id: uuid.UUID,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> int:
    """近 lookback_days 内该 repo 的 style_mismatch + agent_b_attribution=yes 计数。"""
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    stmt = (
        select(func.count())
        .select_from(RejectionReason)
        .join(Issue, Issue.id == RejectionReason.issue_id)
        .where(Issue.repository_id == repo_id)
        .where(RejectionReason.created_at >= since)
        .where(RejectionReason.category == RejectionCategory.STYLE_MISMATCH)
        .where(RejectionReason.agent_b_attribution == AgentBAttribution.YES)
    )
    return int((await session.execute(stmt)).scalar() or 0)


async def maybe_force_refresh(
    session: AsyncSession,
    *,
    issue_id: uuid.UUID,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    threshold: int = DEFAULT_THRESHOLD,
) -> dict[str, object]:
    """检查 issue 所属 repo 是否触发强制刷新；触发时 expires_at 设为 now，
    返回 dict 给 caller 决定是否 send_task（避免在 session 内派发引起 race）。

    返回：
        {"triggered": bool, "repo_id": uuid?, "count": int}
    """
    issue = await session.get(Issue, issue_id)
    if issue is None:
        return {"triggered": False}

    repo_id = issue.repository_id
    count = await count_style_rejections_for_repo(
        session, repo_id, lookback_days=lookback_days,
    )
    if count < threshold:
        return {"triggered": False, "repo_id": str(repo_id), "count": count}

    profile_stmt = select(RepoProfile).where(RepoProfile.repo_id == repo_id)
    profile = (await session.execute(profile_stmt)).scalar_one_or_none()
    if profile is None:
        # 还没 profile，让 CrawlerService 下次自动建即可
        return {"triggered": False, "repo_id": str(repo_id), "count": count,
                "reason": "no_profile_yet"}

    # 标记过期 + forced_refresh_count++（profile_worker 收到任务后会重写整行，
    # 但 forced_refresh_count 需要保留增量计数）
    profile.expires_at = datetime.now(timezone.utc)
    profile.forced_refresh_count = (profile.forced_refresh_count or 0) + 1
    await session.flush()

    log.info(
        "profile_refresh.triggered",
        repo_id=str(repo_id),
        style_mismatch_count=count,
        forced_refresh_count=profile.forced_refresh_count,
    )
    return {
        "triggered": True, "repo_id": str(repo_id),
        "count": count,
        "forced_refresh_count": profile.forced_refresh_count,
    }
