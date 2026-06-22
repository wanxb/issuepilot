"""管理员端点（2.3）—— 手动触发维护任务，便于本地验收与端到端测试。

POST /api/v1/admin/run-maintenance?job=stale_archive_scan|revert_scan
GET  /api/v1/admin/scheduler-jobs

注意：本路由没有鉴权，仅适用于单机本地部署 / dev 环境。多机 / 生产
部署时应加 IP 白名单或简易 token（2.3 不做）。
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, status

from app.scheduler import get_scheduler
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
