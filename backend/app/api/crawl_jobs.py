"""POST /api/v1/crawl-jobs/manual —— 看板手动 URL 入口。

错误码（详见 docs/API_SPEC.md）：
    INVALID_URL             → 400
    REPO_NOT_FOUND          → 404
    REPO_PRIVATE            → 403
    RATE_LIMITED            → 429 (Retry-After)
    CONFIRMATION_REQUIRED   → 409 (前端再次确认重发 force_confirm=true)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.db.database import get_session
from app.github.client import GitHubClient
from app.models.crawl_job import CrawlJob
from app.schemas.crawl import ManualSubmitRequest, ManualSubmitResponse
from app.services.crawler_service import (
    Blacklisted,
    ConfirmationRequired,
    CrawlerService,
    InvalidUrlError,
    RateLimited,
    RepoForbidden,
    RepoNotFound,
)

router = APIRouter(prefix="/api/v1/crawl-jobs", tags=["crawl_jobs"])


async def _get_github_client() -> GitHubClient:
    """FastAPI dependency：每请求一个 client（连接池开销小）。"""
    return GitHubClient.from_settings()


@router.post(
    "/manual",
    response_model=ManualSubmitResponse,
    responses={
        400: {"description": "Invalid URL"},
        403: {"description": "Private repo"},
        404: {"description": "Repo not found"},
        409: {"description": "Confirmation required for large repo"},
        429: {"description": "GitHub rate limited"},
    },
)
async def submit_manual(
    payload: ManualSubmitRequest,
    session: AsyncSession = Depends(get_session),
    github: GitHubClient = Depends(_get_github_client),
) -> JSONResponse | ManualSubmitResponse:
    svc = CrawlerService(session, github)
    try:
        outcome = await svc.from_manual_url(
            raw_url=payload.url,
            max_issues=payload.max_issues,
            force_confirm=payload.force_confirm,
        )
        await session.commit()
    except InvalidUrlError as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": e.code, "message": str(e)},
        ) from e
    except RepoNotFound as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": e.code, "message": str(e)},
        ) from e
    except RepoForbidden as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": e.code, "message": str(e)},
        ) from e
    except RateLimited as e:
        await session.rollback()
        return JSONResponse(
            content={
                "code": e.code,
                "message": str(e),
                "retry_after_seconds": e.retry_after_seconds,
            },
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers=(
                {"Retry-After": str(e.retry_after_seconds)}
                if e.retry_after_seconds is not None else {}
            ),
        )
    except ConfirmationRequired as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": e.code,
                "message": str(e),
                "open_count_total": e.open_count_total,
                "max_issues": e.max_issues,
                "repo_full_name": e.repo_full_name,
            },
        ) from e
    except Blacklisted as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": e.code, "message": str(e),
                    "pattern": e.pattern},
        ) from e
    finally:
        await github.aclose()

    return ManualSubmitResponse(
        job_id=outcome.job_id,
        mode=outcome.mode,  # type: ignore[arg-type]
        repo_full_name=outcome.repo_full_name,
        issues_enqueued=outcome.issues_enqueued,
        issues_reused=outcome.issues_reused,
        issues_skipped_reason=outcome.issues_skipped_reason,
        needs_confirmation=outcome.needs_confirmation,
        open_count_total=outcome.open_count_total,
    )


# ---------------------------------------------------------------------------
# 2.2: GET /api/v1/crawl-jobs —— 抓取日志列表（看板用）
# ---------------------------------------------------------------------------


@router.get("")
async def list_crawl_jobs(
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
) -> dict[str, list[dict]]:
    """返回最近 N 条抓取记录，按 created_at desc 排序。"""
    limit = max(1, min(limit, 200))
    stmt = (
        select(CrawlJob)
        .order_by(CrawlJob.created_at.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": str(r.id),
                "trigger": r.trigger.value,
                "status": r.status.value,
                "input_url": r.input_url,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "stats": r.stats or {},
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }
