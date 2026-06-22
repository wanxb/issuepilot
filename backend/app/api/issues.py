"""GET /api/v1/issues —— 看板列表。

筛选：?status[]=...&min_score=...&language=...&repo=...&source=...
分页：?page=1&page_size=20
排序：?sort=-score   （前缀 - 表示 desc；可选字段 score / created_at / stars）
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.database import get_session
from app.models.dev_task import DevTask
from app.models.enums import DevTaskStatus, IssueSource, IssueStatus
from app.models.evaluation import Evaluation
from app.models.issue import Issue
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.schemas.decide import DecideRequest, PRClosedDecideRequest
from app.schemas.issue import IssueListItem, IssueListResponse
from app.services.issue_service import (
    InvalidDecisionError,
    InvalidTransitionError,
    IssueNotFoundError,
    IssueService,
)
from app.workers.celery_app import celery_app

router = APIRouter(prefix="/api/v1/issues", tags=["issues"])


_SORT_FIELDS = {
    "score": Evaluation.total_score,
    "created_at": Issue.created_at,
    "stars": Repository.stars,
}


@router.get("", response_model=IssueListResponse)
async def list_issues(
    session: AsyncSession = Depends(get_session),
    status_filter: list[IssueStatus] | None = Query(default=None, alias="status"),
    min_score: float | None = Query(default=None, ge=0.0, le=10.0),
    language: str | None = Query(default=None),
    repo: str | None = Query(default=None, description="full_name partial match"),
    source: IssueSource | None = Query(default=None),
    q: str | None = Query(default=None, description="title 部分匹配"),
    sort: str = Query(default="-score"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> IssueListResponse:
    stmt = (
        select(Issue)
        .join(Repository, Repository.id == Issue.repository_id)
        .outerjoin(Evaluation, Evaluation.issue_id == Issue.id)
        .options(
            selectinload(Issue.repository),
            selectinload(Issue.evaluation),
        )
    )
    if status_filter:
        stmt = stmt.where(Issue.status.in_(status_filter))
    if min_score is not None:
        stmt = stmt.where(Evaluation.total_score >= min_score)
    if language:
        stmt = stmt.where(Repository.primary_language.ilike(language))
    if repo:
        stmt = stmt.where(Repository.full_name.ilike(f"%{repo}%"))
    if source:
        stmt = stmt.where(Issue.source == source)
    if q:
        stmt = stmt.where(Issue.title.ilike(f"%{q}%"))

    # 排序
    desc = sort.startswith("-")
    field_name = sort.lstrip("-")
    col = _SORT_FIELDS.get(field_name)
    if col is not None:
        stmt = stmt.order_by(col.desc() if desc else col.asc())
    else:
        stmt = stmt.order_by(Issue.created_at.desc())

    # 计数（用相同 where 条件的简化查询）
    count_stmt = (
        select(func.count())
        .select_from(Issue)
        .join(Repository, Repository.id == Issue.repository_id)
        .outerjoin(Evaluation, Evaluation.issue_id == Issue.id)
    )
    if status_filter:
        count_stmt = count_stmt.where(Issue.status.in_(status_filter))
    if min_score is not None:
        count_stmt = count_stmt.where(Evaluation.total_score >= min_score)
    if language:
        count_stmt = count_stmt.where(Repository.primary_language.ilike(language))
    if repo:
        count_stmt = count_stmt.where(Repository.full_name.ilike(f"%{repo}%"))
    if source:
        count_stmt = count_stmt.where(Issue.source == source)
    if q:
        count_stmt = count_stmt.where(Issue.title.ilike(f"%{q}%"))
    total = int((await session.execute(count_stmt)).scalar() or 0)

    # 分页
    offset = (page - 1) * page_size
    stmt = stmt.offset(offset).limit(page_size)
    rows = (await session.execute(stmt)).scalars().all()

    # 批量查找处于活跃 dev 状态的 DevTask id
    active_task_map: dict[uuid.UUID, uuid.UUID] = {}
    active_issue_ids = [
        r.id for r in rows if r.status in (IssueStatus.IN_DEV, IssueStatus.DEV_TESTING)
    ]
    if active_issue_ids:
        task_stmt = (
            select(DevTask.issue_id, DevTask.id)
            .where(DevTask.issue_id.in_(active_issue_ids))
            .where(DevTask.status.in_([DevTaskStatus.PENDING, DevTaskStatus.RUNNING]))
            .order_by(DevTask.created_at.desc())
        )
        task_rows = (await session.execute(task_stmt)).all()
        for t_issue_id, t_id in task_rows:
            if t_issue_id not in active_task_map:
                active_task_map[t_issue_id] = t_id

    # 1.5e: 批量查找处于 PR_* 状态的最新 PR
    pr_map: dict[uuid.UUID, PullRequest] = {}
    pr_issue_ids = [
        r.id
        for r in rows
        if r.status in (IssueStatus.PR_SUBMITTED, IssueStatus.PR_MERGED, IssueStatus.PR_CLOSED)
    ]
    if pr_issue_ids:
        pr_stmt = (
            select(PullRequest)
            .where(PullRequest.issue_id.in_(pr_issue_ids))
            .order_by(PullRequest.submitted_at.desc())
        )
        pr_rows = (await session.execute(pr_stmt)).scalars().all()
        for pr in pr_rows:
            if pr.issue_id not in pr_map:
                pr_map[pr.issue_id] = pr

    items: list[IssueListItem] = []
    for r in rows:
        item = IssueListItem.model_validate(r)
        item.active_dev_task_id = active_task_map.get(r.id)
        pr_row = pr_map.get(r.id)
        if pr_row is not None:
            from app.schemas.issue import PullRequestView
            item.pull_request = PullRequestView.model_validate(pr_row)
        items.append(item)

    return IssueListResponse(items=items, page=page, page_size=page_size, total=total)


@router.post(
    "/{issue_id}/decide",
    response_model=IssueListItem,
    responses={
        404: {"description": "Issue not found"},
        409: {"description": "Issue not in PENDING_DECISION state"},
        422: {"description": "Unknown action"},
    },
)
async def decide(
    issue_id: uuid.UUID,
    payload: DecideRequest,
    session: AsyncSession = Depends(get_session),
) -> IssueListItem:
    svc = IssueService(session)
    try:
        issue = await svc.get(issue_id)
    except IssueNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ISSUE_NOT_FOUND", "message": str(e)},
        ) from e

    dev_task: DevTask | None = None
    try:
        issue = await svc.decide(issue, action=payload.action)

        # start_dev：创建 DevTask，后续 trigger dev_worker
        if payload.action == "start_dev":
            dev_task = DevTask(
                issue_id=issue.id,
                attempt_number=1,
                status=DevTaskStatus.PENDING,
            )
            session.add(dev_task)
            await session.flush()

        await session.commit()
    except InvalidDecisionError as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_ACTION", "message": str(e)},
        ) from e
    except InvalidTransitionError as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "INVALID_STATE_TRANSITION",
                "message": str(e),
                "from_state": e.from_state.value,
                "to_state": e.to_state.value,
            },
        ) from e

    # 任务入队（commit 后再 send，避免 worker 比 commit 更快执行）
    if dev_task is not None:
        celery_app.send_task(
            "app.workers.dev_worker.develop_issue",
            args=[str(issue.id), str(dev_task.id)],
            queue="dev_queue",
        )

    # 重读一次以让关系加载齐全
    fresh = await svc.get(issue.id)
    item = IssueListItem.model_validate(fresh)
    if dev_task is not None:
        item.active_dev_task_id = dev_task.id
    return item


# ---------------------------------------------------------------------------
# 2.3: PR 关闭后人工决定 —— restart_dev | archive
# ---------------------------------------------------------------------------


@router.post(
    "/{issue_id}/pr-closed-decide",
    response_model=IssueListItem,
    responses={
        404: {"description": "Issue not found"},
        409: {"description": "Issue not in PR_CLOSED state"},
        422: {"description": "Unknown action"},
    },
)
async def pr_closed_decide(
    issue_id: uuid.UUID,
    payload: PRClosedDecideRequest,
    session: AsyncSession = Depends(get_session),
) -> IssueListItem:
    """maintainer 关掉 PR 后由人工决定：

    - restart_dev：Issue PR_CLOSED → QUEUED_DEV，重建 DevTask 入 dev_queue
    - archive    ：Issue PR_CLOSED → ARCHIVED，终态
    """
    svc = IssueService(session)
    try:
        issue = await svc.get(issue_id)
    except IssueNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ISSUE_NOT_FOUND", "message": str(e)},
        ) from e

    if issue.status != IssueStatus.PR_CLOSED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "INVALID_STATE",
                "message": (
                    f"pr-closed-decide only works on PR_CLOSED issues; "
                    f"current={issue.status.value}"
                ),
                "current": issue.status.value,
            },
        )

    dev_task: DevTask | None = None
    try:
        if payload.action == "archive":
            await svc.mark_archived(issue, reason="user_archived_after_pr_closed")
        elif payload.action == "restart_dev":
            # 计算 attempt_number（同 issue 已有 dev_task 数 + 1）
            from sqlalchemy import func as _func
            count = (await session.execute(
                select(_func.count(DevTask.id)).where(DevTask.issue_id == issue.id)
            )).scalar_one()
            dev_task = DevTask(
                issue_id=issue.id,
                attempt_number=int(count) + 1,
                status=DevTaskStatus.PENDING,
                review_context=(
                    "Previous PR was closed by maintainer without merge. "
                    "Re-read the PR comments / maintainer review (visible in "
                    "rejection_reasons + pr_outcomes) before starting; "
                    "address the rejection reasons explicitly."
                ),
            )
            session.add(dev_task)
            await session.flush()
            await svc.re_queue_dev(issue)  # PR_CLOSED → QUEUED_DEV
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "INVALID_ACTION",
                        "message": f"unknown action: {payload.action!r}"},
            )

        await session.commit()
    except InvalidTransitionError as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "INVALID_STATE_TRANSITION",
                "message": str(e),
                "from_state": e.from_state.value,
                "to_state": e.to_state.value,
            },
        ) from e

    # commit 后入队（同 decide）
    if dev_task is not None:
        celery_app.send_task(
            "app.workers.dev_worker.develop_issue",
            args=[str(issue.id), str(dev_task.id)],
            queue="dev_queue",
        )

    fresh = await svc.get(issue.id)
    item = IssueListItem.model_validate(fresh)
    if dev_task is not None:
        item.active_dev_task_id = dev_task.id
    return item


# ---------------------------------------------------------------------------
# 2.4d: Issue 详情页（评估 + 历次开发/评审 + PR + rejection 摘要）
# ---------------------------------------------------------------------------


@router.get("/{issue_id}/detail")
async def issue_detail(
    issue_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """返回单个 issue 的完整上下文：所有 dev_tasks / review_tasks / pull_request /
    rejection_reasons 摘要 + evaluation。"""
    from app.models.dev_task import DevTask
    from app.models.review_task import ReviewTask
    from app.models.pull_request import PullRequest
    from app.models.rejection_reason import RejectionReason
    from sqlalchemy import select as _select

    issue_stmt = (
        _select(Issue)
        .where(Issue.id == issue_id)
        .options(
            selectinload(Issue.repository),
            selectinload(Issue.evaluation),
        )
    )
    issue = (await session.execute(issue_stmt)).scalar_one_or_none()
    if issue is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ISSUE_NOT_FOUND",
                    "message": f"issue {issue_id} not found"},
        )

    dev_tasks = (await session.execute(
        _select(DevTask).where(DevTask.issue_id == issue_id)
        .order_by(DevTask.attempt_number.asc(), DevTask.created_at.asc())
    )).scalars().all()

    review_tasks = (await session.execute(
        _select(ReviewTask).where(ReviewTask.issue_id == issue_id)
        .order_by(ReviewTask.attempt_number.asc(), ReviewTask.created_at.asc())
    )).scalars().all()

    pr = (await session.execute(
        _select(PullRequest).where(PullRequest.issue_id == issue_id)
        .order_by(PullRequest.submitted_at.desc()).limit(1)
    )).scalar_one_or_none()

    rejections = (await session.execute(
        _select(RejectionReason).where(RejectionReason.issue_id == issue_id)
        .order_by(RejectionReason.created_at.asc())
    )).scalars().all()

    ev = issue.evaluation
    return {
        "id": str(issue.id),
        "status": issue.status.value,
        "github_url": issue.github_url,
        "github_number": issue.github_number,
        "title": issue.title,
        "body": issue.body,
        "labels": list(issue.labels or []),
        "author": issue.author,
        "source": issue.source.value,
        "created_at": issue.created_at.isoformat(),
        "updated_at": issue.updated_at.isoformat(),
        "repository": {
            "full_name": issue.repository.full_name,
            "primary_language": issue.repository.primary_language,
            "stars": issue.repository.stars,
        },
        "evaluation": None if ev is None else {
            "total_score": ev.total_score,
            "difficulty": ev.difficulty,
            "estimated_hours": ev.estimated_hours,
            "summary": ev.summary,
            "recommendation": ev.recommendation,
            "is_worth_developing": ev.is_worth_developing,
            "dimensions": ev.dimensions,
            "model": ev.model,
            "provider": ev.provider,
            "is_fallback": ev.is_fallback,
            "created_at": ev.created_at.isoformat(),
        },
        "dev_tasks": [
            {
                "id": str(t.id),
                "attempt_number": t.attempt_number,
                "status": t.status.value,
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "finished_at": t.finished_at.isoformat() if t.finished_at else None,
                "forked_repo": t.forked_repo,
                "branch_name": t.branch_name,
                "branch_pushed": t.branch_pushed,
                "use_fallback_provider": t.use_fallback_provider,
                "files_changed": list(t.files_changed or []),
                "diff_summary": t.diff_summary,
                "loop_iterations": t.loop_iterations,
                "total_cost_usd": t.total_cost_usd,
                "failure_reason": t.failure_reason,
                "failure_detail": t.failure_detail,
                "test_result": t.test_result,
                "review_context": t.review_context,
            }
            for t in dev_tasks
        ],
        "review_tasks": [
            {
                "id": str(r.id),
                "dev_task_id": str(r.dev_task_id) if r.dev_task_id else None,
                "attempt_number": r.attempt_number,
                "status": r.status.value,
                "verdict": r.verdict.value if r.verdict else None,
                "overall_score": r.overall_score,
                "dimensions": r.dimensions,
                "rejection_reason": r.rejection_reason,
                "overall_comment": r.overall_comment,
                "pr_title": r.pr_title,
                "pr_body": r.pr_body,
                "provider": r.provider,
                "model": r.model,
                "is_fallback": r.is_fallback,
                "cost_usd": r.cost_usd,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in review_tasks
        ],
        "pull_request": None if pr is None else {
            "github_pr_number": pr.github_pr_number,
            "github_pr_url": pr.github_pr_url,
            "title": pr.title,
            "base_repo": pr.base_repo,
            "base_branch": pr.base_branch,
            "head_repo": pr.head_repo,
            "head_branch": pr.head_branch,
            "status": pr.status.value,
            "final_outcome": pr.final_outcome.value if pr.final_outcome else None,
            "submitted_at": pr.submitted_at.isoformat() if pr.submitted_at else None,
            "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
            "closed_at": pr.closed_at.isoformat() if pr.closed_at else None,
            "merger_login": pr.merger_login,
            "closer_login": pr.closer_login,
        },
        "rejection_reasons": [
            {
                "id": str(r.id),
                "source": r.source.value,
                "category": r.category.value,
                "severity": r.severity.value,
                "dimension": r.dimension.value if r.dimension else None,
                "agent_b_attribution": r.agent_b_attribution.value
                if r.agent_b_attribution else None,
                "detail": r.detail,
                "classified_by": r.classified_by,
                "created_at": r.created_at.isoformat(),
            }
            for r in rejections
        ],
    }
