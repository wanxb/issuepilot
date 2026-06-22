"""管理员端点（2.3）—— 手动触发维护任务，便于本地验收与端到端测试。

POST /api/v1/admin/run-maintenance?job=stale_archive_scan|revert_scan
GET  /api/v1/admin/scheduler-jobs

注意：本路由没有鉴权，仅适用于单机本地部署 / dev 环境。多机 / 生产
部署时应加 IP 白名单或简易 token（2.3 不做）。
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
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
