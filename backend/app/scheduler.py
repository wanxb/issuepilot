"""APScheduler in-process scheduler（2.3）。

启动在 API 容器内（FastAPI lifespan hook）。每天固定时间触发 Celery
maintenance 任务（实际执行在 worker 容器）：

    - 03:07 UTC   stale_archive_scan  IGNORED >30d → ARCHIVED
    - 03:17 UTC   revert_scan         MERGED PR 是否被 revert

时间选 :07 / :17 是为了不撞 cron 高峰整点，错开仓库 API 限流潮汐。

技术债（ROADMAP §技术债）：单机部署用 APScheduler；扩到多 API 副本时
迁移到 Celery beat 或独立 beat 容器，避免重复触发。
"""
from __future__ import annotations

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

log = structlog.get_logger(__name__)


_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler


def init_scheduler() -> AsyncIOScheduler:
    """构建并启动 scheduler；幂等。"""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    sched = AsyncIOScheduler(timezone="UTC")

    sched.add_job(
        _trigger_stale_archive,
        trigger=CronTrigger(hour=3, minute=7, timezone="UTC"),
        id="stale_archive_scan",
        replace_existing=True,
        misfire_grace_time=600,
    )
    sched.add_job(
        _trigger_revert_scan,
        trigger=CronTrigger(hour=3, minute=17, timezone="UTC"),
        id="revert_scan",
        replace_existing=True,
        misfire_grace_time=600,
    )

    sched.start()
    log.info("scheduler.started",
             jobs=[j.id for j in sched.get_jobs()])
    _scheduler = sched
    return sched


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        log.info("scheduler.shutdown")
        _scheduler = None


# ---------------------------------------------------------------------------
# 触发包装：在 API 容器构造 Celery send_task，让任务跑在 worker 容器
# ---------------------------------------------------------------------------


def _trigger_stale_archive() -> None:
    from app.workers.celery_app import celery_app

    task_id = celery_app.send_task(
        "app.workers.maintenance_worker.stale_archive_scan",
        queue="classify_queue",
    ).id
    log.info("scheduler.dispatched", job="stale_archive_scan", task_id=task_id)


def _trigger_revert_scan() -> None:
    from app.workers.celery_app import celery_app

    task_id = celery_app.send_task(
        "app.workers.maintenance_worker.revert_scan",
        queue="classify_queue",
    ).id
    log.info("scheduler.dispatched", job="revert_scan", task_id=task_id)
