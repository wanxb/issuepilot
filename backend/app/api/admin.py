"""管理员端点（2.3）—— 手动触发维护任务，便于本地验收与端到端测试。

POST /api/v1/admin/run-maintenance?job=stale_archive_scan|revert_scan
GET  /api/v1/admin/scheduler-jobs

注意：本路由没有鉴权，仅适用于单机本地部署 / dev 环境。多机 / 生产
部署时应加 IP 白名单或简易 token（2.3 不做）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.llm.factory import _load_yaml, clear_cache as clear_models_yaml_cache
from app.models.llm_call_log import LLMCallLog
from app.scheduler import (
    get_scheduler,
    reload_crawl_target,
    unregister_crawl_target,
)
from app.services.crawl_target_service import CrawlTargetService
from app.workers.maintenance_worker import trigger

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


JobName = Literal["stale_archive_scan", "revert_scan"]


@router.post("/run-maintenance")
async def run_maintenance(job: JobName) -> dict[str, str]:
    """同步派发 Celery 任务到 classify_queue，返回 task_id。"""
    try:
        task_id = trigger(job)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "DISPATCH_FAILED", "message": str(e)[:300]},
        ) from e
    return {"job": job, "task_id": task_id, "queue": "classify_queue"}


@router.get("/scheduler-jobs")
async def scheduler_jobs() -> dict[str, object]:
    """诊断：返回 APScheduler 当前注册的 job + 下次触发时间。"""
    sched = get_scheduler()
    if sched is None:
        return {"running": False, "jobs": []}
    return {
        "running": sched.running,
        "jobs": [
            {
                "id": j.id,
                "next_run_time": j.next_run_time.isoformat()
                if j.next_run_time else None,
                "trigger": str(j.trigger),
            }
            for j in sched.get_jobs()
        ],
    }


# ---------------------------------------------------------------------------
# 2.2: scheduler reload —— CLI 修改 crawl_targets 后调用此端点同步 schedule
# ---------------------------------------------------------------------------


@router.post("/scheduler-reload")
async def scheduler_reload(
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """全量重载 crawl_targets：未启用的从 scheduler 摘掉；启用的 add/update。"""
    sched = get_scheduler()
    if sched is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "SCHEDULER_NOT_RUNNING"},
        )

    svc = CrawlTargetService(session)
    all_targets = await svc.list_all()

    registered = 0
    unregistered = 0
    for t in all_targets:
        if t.enabled:
            reload_crawl_target(str(t.id), t.name, t.cron)
            registered += 1
        else:
            unregister_crawl_target(str(t.id))
            unregistered += 1

    return {
        "registered": registered,
        "unregistered": unregistered,
        "jobs": [j.id for j in sched.get_jobs()],
    }


# ---------------------------------------------------------------------------
# 2.5: 成本统计 —— 聚合 llm_call_logs
# ---------------------------------------------------------------------------


@router.get("/cost-stats")
async def cost_stats(
    hours: int = Query(default=168, ge=1, le=24 * 90),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """按 agent_kind × provider × model 聚合最近 N 小时（默认 7 天）成本。"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    stmt = (
        select(
            LLMCallLog.agent_kind,
            LLMCallLog.provider,
            LLMCallLog.model,
            func.count().label("calls"),
            func.sum(LLMCallLog.input_tokens).label("in_tok"),
            func.sum(LLMCallLog.output_tokens).label("out_tok"),
            func.sum(LLMCallLog.cost_usd).label("cost"),
            func.count().filter(LLMCallLog.is_fallback.is_(True)).label("fallback_calls"),
            func.count().filter(LLMCallLog.success.is_(False)).label("failed_calls"),
        )
        .where(LLMCallLog.created_at >= cutoff)
        .group_by(LLMCallLog.agent_kind, LLMCallLog.provider, LLMCallLog.model)
        .order_by(func.sum(LLMCallLog.cost_usd).desc())
    )
    rows = (await session.execute(stmt)).all()

    items = [
        {
            "agent_kind": r.agent_kind.value if r.agent_kind else None,
            "provider": r.provider,
            "model": r.model,
            "calls": int(r.calls or 0),
            "input_tokens": int(r.in_tok or 0),
            "output_tokens": int(r.out_tok or 0),
            "cost_usd": float(r.cost or 0),
            "fallback_calls": int(r.fallback_calls or 0),
            "failed_calls": int(r.failed_calls or 0),
        }
        for r in rows
    ]
    total_cost = sum(it["cost_usd"] for it in items)
    total_calls = sum(it["calls"] for it in items)
    total_fail = sum(it["failed_calls"] for it in items)
    return {
        "since": cutoff.isoformat(),
        "hours": hours,
        "items": items,
        "totals": {
            "calls": total_calls,
            "cost_usd": total_cost,
            "failure_rate": (total_fail / total_calls) if total_calls else 0.0,
        },
    }


# ---------------------------------------------------------------------------
# 2.5: models.yaml 热加载（无需重启 api / worker）
# ---------------------------------------------------------------------------


@router.post("/reload-models")
async def reload_models() -> dict[str, object]:
    """清空 _load_yaml 的 lru_cache，下次 build_client 会重新读 models.yaml。

    注意：已经构造出的 LLMClient 实例不会动；只对调用 build_client 后新建的
    client 生效。Celery 任务通常每次 task 都新建 client（review_worker /
    dev_worker 等），所以热加载效果即时；如果某 service 是长生命周期 client，
    需要它自己重新拉。
    """
    clear_models_yaml_cache()
    try:
        data = _load_yaml()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "MODELS_YAML_RELOAD_FAILED",
                    "message": str(e)[:300]},
        ) from e
    agents = list((data.get("agents") or {}).keys())
    return {"reloaded": True, "agents": agents}


# ---------------------------------------------------------------------------
# 3.3: Blacklist 管理
# ---------------------------------------------------------------------------


from pydantic import BaseModel  # noqa: E402

from app.services.blacklist_service import BlacklistService  # noqa: E402


class BlacklistAddRequest(BaseModel):
    entity_type: Literal["repo", "issue"]
    pattern: str
    reason: str | None = None
    enabled: bool = True


@router.get("/blacklist")
async def list_blacklist(
    session: AsyncSession = Depends(get_session),
) -> dict[str, list[dict[str, object]]]:
    rows = await BlacklistService(session).list_all()
    return {
        "items": [
            {
                "id": str(r.id),
                "entity_type": r.entity_type,
                "pattern": r.pattern,
                "reason": r.reason,
                "enabled": r.enabled,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


@router.post("/blacklist")
async def add_blacklist(
    payload: BlacklistAddRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    try:
        row = await BlacklistService(session).add(
            entity_type=payload.entity_type,
            pattern=payload.pattern,
            reason=payload.reason,
            enabled=payload.enabled,
        )
        await session.commit()
    except ValueError as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_BLACKLIST", "message": str(e)},
        ) from e
    return {"id": str(row.id), "pattern": row.pattern}


@router.delete("/blacklist/{blacklist_id}")
async def delete_blacklist(
    blacklist_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    ok = await BlacklistService(session).delete(blacklist_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND"},
        )
    await session.commit()
    return {"deleted": True}
