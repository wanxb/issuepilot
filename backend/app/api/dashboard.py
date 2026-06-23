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
from app.models.dev_task import DevTask
from app.models.enums import (
    DevTaskStatus,
    IssueStatus,
    PRFinalOutcome,
    RejectionCategory,
    RejectionDimension,
    RejectionSource,
    ReviewVerdict,
)
from app.models.issue import Issue
from app.models.llm_call_log import LLMCallLog
from app.models.pr_outcome import PROutcome
from app.models.pull_request import PullRequest
from app.models.rejection_reason import RejectionReason
from app.models.repository import Repository
from app.models.review_task import ReviewTask

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


# ---------------------------------------------------------------------------
# 3.1: 周报（PR 学习闭环聚合）
# ---------------------------------------------------------------------------


@router.get("/weekly-report")
async def weekly_report(
    days: int = Query(default=7, ge=1, le=90),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """聚合最近 N 天的关键质量指标，前端 WeeklyReportPanel 使用。

    含：
      - top_failure_modes Top 3 (复用 pr_failures 逻辑但裁到 3)
      - cost_by_agent 各 agent 类型成本
      - attribution_ratio Agent B 归因比例
      - issue_funnel 漏斗（DISCOVERED → PR_MERGED）
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # 1) Top 3 失败模式（仅展示 category × attribution 计数，不带 samples）
    top_stmt = (
        select(
            RejectionReason.category,
            RejectionReason.agent_b_attribution,
            func.count().label("n"),
        )
        .where(RejectionReason.created_at >= since)
        .group_by(RejectionReason.category, RejectionReason.agent_b_attribution)
        .order_by(func.count().desc())
        .limit(3)
    )
    top_rows = (await session.execute(top_stmt)).all()
    top_failure_modes = [
        {
            "category": r.category.value if r.category else "other",
            "agent_b_attribution": r.agent_b_attribution.value
            if r.agent_b_attribution else "unclear",
            "count": int(r.n),
        }
        for r in top_rows
    ]

    # 2) cost_by_agent
    cost_stmt = (
        select(
            LLMCallLog.agent_kind,
            func.count().label("calls"),
            func.sum(LLMCallLog.cost_usd).label("cost"),
            func.sum(LLMCallLog.input_tokens).label("in_tok"),
            func.sum(LLMCallLog.output_tokens).label("out_tok"),
            func.count().filter(LLMCallLog.is_fallback.is_(True)).label("fb"),
        )
        .where(LLMCallLog.created_at >= since)
        .group_by(LLMCallLog.agent_kind)
        .order_by(func.sum(LLMCallLog.cost_usd).desc())
    )
    cost_rows = (await session.execute(cost_stmt)).all()
    cost_by_agent = [
        {
            "agent_kind": r.agent_kind.value if r.agent_kind else None,
            "calls": int(r.calls or 0),
            "cost_usd": float(r.cost or 0),
            "input_tokens": int(r.in_tok or 0),
            "output_tokens": int(r.out_tok or 0),
            "fallback_calls": int(r.fb or 0),
        }
        for r in cost_rows
    ]

    # 3) Attribution ratio
    attr_stmt = (
        select(RejectionReason.agent_b_attribution, func.count())
        .where(RejectionReason.created_at >= since)
        .group_by(RejectionReason.agent_b_attribution)
    )
    attr_rows = (await session.execute(attr_stmt)).all()
    attr_total = sum(int(r[1]) for r in attr_rows) or 1
    attribution_ratio = {
        (r[0].value if r[0] else "unclear"): {
            "count": int(r[1]),
            "ratio": int(r[1]) / attr_total,
        }
        for r in attr_rows
    }

    # 4) Issue funnel
    funnel_stmt = (
        select(Issue.status, func.count())
        .group_by(Issue.status)
    )
    funnel_rows = (await session.execute(funnel_stmt)).all()
    issue_funnel = {r[0].value: int(r[1]) for r in funnel_rows}

    # 5) PR 终态
    pr_stmt = (
        select(PullRequest.final_outcome, func.count())
        .where(PullRequest.updated_at >= since)
        .group_by(PullRequest.final_outcome)
    )
    pr_rows = (await session.execute(pr_stmt)).all()
    pr_outcomes = {
        (r[0].value if r[0] else "null"): int(r[1]) for r in pr_rows
    }

    total_cost = sum(it["cost_usd"] for it in cost_by_agent)
    total_calls = sum(it["calls"] for it in cost_by_agent)
    return {
        "since": since.isoformat(),
        "days": days,
        "top_failure_modes": top_failure_modes,
        "cost_by_agent": cost_by_agent,
        "totals": {
            "calls": total_calls,
            "cost_usd": total_cost,
        },
        "attribution_ratio": attribution_ratio,
        "issue_funnel": issue_funnel,
        "pr_outcomes": pr_outcomes,
    }


# ---------------------------------------------------------------------------
# 3.1: Agent B 成功率 / Agent C 误拒率分析
# ---------------------------------------------------------------------------


@router.get("/agent-quality")
async def agent_quality(
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Agent B / Agent C 质量指标聚合。"""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # ---- Agent B 成功率（按 dev_task.status × use_fallback_provider × language）
    dev_by_status_stmt = (
        select(DevTask.status, func.count())
        .where(DevTask.created_at >= since)
        .group_by(DevTask.status)
    )
    dev_status_rows = (await session.execute(dev_by_status_stmt)).all()
    dev_by_status = {r[0].value: int(r[1]) for r in dev_status_rows}
    dev_total = sum(dev_by_status.values()) or 1
    dev_succeeded = dev_by_status.get(DevTaskStatus.SUCCEEDED.value, 0)
    dev_success_rate = dev_succeeded / dev_total

    # 按 fallback
    fb_stmt = (
        select(
            DevTask.use_fallback_provider,
            DevTask.status,
            func.count(),
        )
        .where(DevTask.created_at >= since)
        .group_by(DevTask.use_fallback_provider, DevTask.status)
    )
    fb_rows = (await session.execute(fb_stmt)).all()
    fallback_split: dict[str, dict[str, int]] = {"primary": {}, "fallback": {}}
    for r in fb_rows:
        bucket = "fallback" if r[0] else "primary"
        fallback_split[bucket][r[1].value] = int(r[2])

    # 按语言（join repositories）
    lang_stmt = (
        select(
            Repository.primary_language,
            DevTask.status,
            func.count(),
        )
        .join(Issue, Issue.id == DevTask.issue_id)
        .join(Repository, Repository.id == Issue.repository_id)
        .where(DevTask.created_at >= since)
        .group_by(Repository.primary_language, DevTask.status)
    )
    lang_rows = (await session.execute(lang_stmt)).all()
    by_language: dict[str, dict[str, int]] = {}
    for r in lang_rows:
        lang = r[0] or "unknown"
        by_language.setdefault(lang, {})[r[1].value] = int(r[2])

    # 按 failure_reason
    fr_stmt = (
        select(DevTask.failure_reason, func.count())
        .where(DevTask.created_at >= since)
        .where(DevTask.failure_reason.isnot(None))
        .group_by(DevTask.failure_reason)
        .order_by(func.count().desc())
    )
    fr_rows = (await session.execute(fr_stmt)).all()
    by_failure_reason = {(r[0] or "unknown"): int(r[1]) for r in fr_rows}

    # ---- Agent C 误拒率（review_task REJECTED 与 maintainer 终态对比）
    # 简化版：count review REJECTED 总数 vs PR_CLOSED_BY_MAINTAINER（同 issue）
    review_stmt = (
        select(ReviewTask.verdict, func.count())
        .where(ReviewTask.created_at >= since)
        .where(ReviewTask.verdict.isnot(None))
        .group_by(ReviewTask.verdict)
    )
    review_rows = (await session.execute(review_stmt)).all()
    review_by_verdict = {
        (r[0].value if r[0] else "unknown"): int(r[1]) for r in review_rows
    }

    # Agent C APPROVED → maintainer MERGED_CLEAN：吻合
    # Agent C APPROVED → maintainer CLOSED_BY_MAINTAINER：误判（漏拒）
    # Agent C REJECTED → 没 PR / 经 retry 后 merged：误拒（待人工 review）
    approved_merged = (await session.execute(
        select(func.count()).select_from(ReviewTask)
        .join(PullRequest, PullRequest.review_task_id == ReviewTask.id)
        .where(ReviewTask.verdict == ReviewVerdict.APPROVED)
        .where(PullRequest.final_outcome == PRFinalOutcome.MERGED_CLEAN)
        .where(ReviewTask.created_at >= since)
    )).scalar_one()
    approved_closed = (await session.execute(
        select(func.count()).select_from(ReviewTask)
        .join(PullRequest, PullRequest.review_task_id == ReviewTask.id)
        .where(ReviewTask.verdict == ReviewVerdict.APPROVED)
        .where(PullRequest.final_outcome == PRFinalOutcome.CLOSED_BY_MAINTAINER)
        .where(ReviewTask.created_at >= since)
    )).scalar_one()

    return {
        "since": since.isoformat(),
        "days": days,
        "agent_b": {
            "total": dev_total,
            "success_rate": dev_success_rate,
            "by_status": dev_by_status,
            "fallback_split": fallback_split,
            "by_language": by_language,
            "by_failure_reason": by_failure_reason,
        },
        "agent_c": {
            "by_verdict": review_by_verdict,
            "approved_merged_clean": int(approved_merged or 0),
            "approved_closed_by_maintainer": int(approved_closed or 0),
            "maintainer_agreement_note": (
                "approved_merged_clean / (approved_merged_clean + "
                "approved_closed_by_maintainer) 越高越好；当前 PR 量过低"
                "(<10) 时数字仅供参考"
            ),
        },
    }
