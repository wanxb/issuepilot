"""GET /api/v1/issues —— 看板列表。

筛选：?status[]=...&min_score=...&language=...&repo=...&source=...
分页：?page=1&page_size=20
排序：?sort=-score   （前缀 - 表示 desc；可选字段 score / created_at / stars）
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.database import get_session
from app.models.enums import IssueSource, IssueStatus
from app.models.evaluation import Evaluation
from app.models.issue import Issue
from app.models.repository import Repository
from app.schemas.decide import DecideRequest
from app.schemas.issue import IssueListItem, IssueListResponse
from app.services.issue_service import (
    InvalidDecisionError,
    InvalidTransitionError,
    IssueNotFoundError,
    IssueService,
)

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
    total = int((await session.execute(count_stmt)).scalar() or 0)

    # 分页
    offset = (page - 1) * page_size
    stmt = stmt.offset(offset).limit(page_size)
    rows = (await session.execute(stmt)).scalars().all()

    items = [IssueListItem.model_validate(r) for r in rows]
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

    try:
        issue = await svc.decide(issue, action=payload.action)
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

    # 重读一次以让关系加载齐全
    fresh = await svc.get(issue.id)
    return IssueListItem.model_validate(fresh)
