"""Dashboard 聚合接口（2.4）。

只读、低频、纯 SQL 聚合；前端首页 widget + PR 失败复盘视图调。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.models.enums import (
    IssueStatus,
    PRFinalOutcome,
    RejectionCategory,
    RejectionDimension,
    RejectionSource,
)
from app.models.issue import Issue
from app.models.llm_call_log import LLMCallLog
from app.models.pr_outcome import PROutcome
from app.models.pull_request import PullRequest
from app.models.rejection_reason import RejectionReason

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# Stats overview
# ---------------------------------------------------------------------------


@router.get("/stats")
async def stats_overview(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """各状态 issue 数 + 今日新增 + 本周 PR_MERGED/CLOSED + 本周 fallback 率。"""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now.weekday())  # ISO 周一 00:00

    # --- 各 status 计数 ---
    status_stmt = (
        select(Issue.status, func.count().label("n"))
        .group_by(Issue.status)
    )
    status_rows = (await session.execute(status_stmt)).all()
    by_status = {r.status.value: int(r.n) for r in status_rows}

    # 缺失状态补 0，方便前端直接读
    for s in IssueStatus:
        by_status.setdefault(s.value, 0)

    # --- 今日新增 ---
    today_new = (await session.execute(
        select(func.count())
        .select_from(Issue)
        .where(Issue.created_at >= today_start)
    )).scalar_one()

    # --- 本周 PR 终态 ---
    pr_week_stmt = (
        select(PullRequest.final_outcome, func.count())
        .where(PullRequest.updated_at >= week_start)
        .group_by(PullRequest.final_outcome)
    )
    pr_rows = (await session.execute(pr_week_stmt)).all()
    pr_week = {
        (r[0].value if r[0] else "null"): int(r[1]) for r in pr_rows
    }

    # --- 本周 LLM 调用 & fallback 触发率 ---
    llm_week_stmt = select(
        func.count().label("total"),
        func.sum(case((LLMCallLog.is_fallback.is_(True), 1), else_=0)).label("fb"),
        func.sum(case((LLMCallLog.success.is_(False), 1), else_=0)).label("fail"),
        func.sum(LLMCallLog.cost_usd).label("cost"),
    ).where(LLMCallLog.created_at >= week_start)
    row = (await session.execute(llm_week_stmt)).one()
    total_calls = int(row.total or 0)
    fallback_calls = int(row.fb or 0)
    failed_calls = int(row.fail or 0)
    week_cost = float(row.cost or 0.0)

    return {
        "as_of": now.isoformat(),
        "by_status": by_status,
        "today_new_issues": int(today_new),
        "this_week_pr": pr_week,
        "this_week_llm": {
            "calls": total_calls,
            "fallback_calls": fallback_calls,
            "fallback_rate": (fallback_calls / total_calls) if total_calls else 0.0,
            "failed_calls": failed_calls,
            "failure_rate": (failed_calls / total_calls) if total_calls else 0.0,
            "cost_usd": week_cost,
        },
    }


# ---------------------------------------------------------------------------
# PR 失败复盘
# ---------------------------------------------------------------------------


@router.get("/pr-failures")
async def pr_failures(
    days: int = Query(default=30, ge=1, le=365),
    top_n_samples: int = Query(default=3, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """按 (category, agent_b_attribution) × source 聚合 rejection_reasons。

    返回：
        - by_category：每 category 的总数 + Agent B 归因明细 + 样本 PR
        - by_dimension：Agent C 评分维度的失败计数
        - by_source：agent_c / maintainer_review / maintainer_close 计数
        - top_failure_modes：Top N (category, attribution) 组合 + 样本 PR
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # --- by_category × attribution ---
    cat_stmt = (
        select(
            RejectionReason.category,
            RejectionReason.agent_b_attribution,
            func.count().label("n"),
        )
        .where(RejectionReason.created_at >= since)
        .group_by(
            RejectionReason.category, RejectionReason.agent_b_attribution,
        )
        .order_by(func.count().desc())
    )
    cat_rows = (await session.execute(cat_stmt)).all()
    by_category: dict[str, dict[str, Any]] = {}
    top_modes: list[dict[str, Any]] = []
    for r in cat_rows:
        cat = r.category.value if r.category else "other"
        attr = r.agent_b_attribution.value if r.agent_b_attribution else "unclear"
        n = int(r.n)
        bucket = by_category.setdefault(cat, {"total": 0, "by_attribution": {}})
        bucket["total"] += n
        bucket["by_attribution"][attr] = n
        top_modes.append({
            "category": cat, "agent_b_attribution": attr, "count": n,
        })

    # --- by_dimension ---
    dim_stmt = (
        select(RejectionReason.dimension, func.count())
        .where(RejectionReason.created_at >= since)
        .group_by(RejectionReason.dimension)
    )
    dim_rows = (await session.execute(dim_stmt)).all()
    by_dimension = {
        (r[0].value if r[0] else "null"): int(r[1]) for r in dim_rows
    }

    # --- by_source ---
    src_stmt = (
        select(RejectionReason.source, func.count())
        .where(RejectionReason.created_at >= since)
        .group_by(RejectionReason.source)
    )
    src_rows = (await session.execute(src_stmt)).all()
    by_source = {r[0].value: int(r[1]) for r in src_rows}

    # --- top_n_samples 个 sample PR for top modes ---
    samples_by_mode: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for mode in top_modes[: min(len(top_modes), 5)]:
        # 取该 (category, attribution) 组合最近的 N 条样本
        cat_enum = RejectionCategory(mode["category"])
        attr_str = mode["agent_b_attribution"]
        samples_stmt = (
            select(
                RejectionReason.id,
                RejectionReason.detail,
                RejectionReason.source,
                RejectionReason.created_at,
                RejectionReason.pr_id,
                PullRequest.github_pr_url,
                PullRequest.github_pr_number,
                PullRequest.title,
            )
            .outerjoin(PullRequest, PullRequest.id == RejectionReason.pr_id)
            .where(RejectionReason.category == cat_enum)
            .where(RejectionReason.created_at >= since)
            .order_by(RejectionReason.created_at.desc())
            .limit(top_n_samples)
        )
        if attr_str == "unclear":
            samples_stmt = samples_stmt  # 不加 attribution 过滤（unclear 太宽）
        rows = (await session.execute(samples_stmt)).all()
        samples_by_mode[(mode["category"], attr_str)] = [
            {
                "rejection_id": str(s.id),
                "pr_url": s.github_pr_url,
                "pr_number": s.github_pr_number,
                "pr_title": s.title,
                "source": s.source.value,
                "detail_preview": (s.detail or "")[:200],
                "created_at": s.created_at.isoformat(),
            }
            for s in rows
        ]

    # 把 samples 塞到 top_modes
    for mode in top_modes:
        key = (mode["category"], mode["agent_b_attribution"])
        mode["samples"] = samples_by_mode.get(key, [])

    return {
        "since": since.isoformat(),
        "days": days,
        "by_category": by_category,
        "by_dimension": by_dimension,
        "by_source": by_source,
        "top_failure_modes": top_modes[:10],
    }
