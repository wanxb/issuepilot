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

    # 2.2: 启动后异步加载 enabled crawl_targets，按 cron 注册
    import asyncio as _asyncio
    _asyncio.create_task(_load_crawl_targets(sched))

    log.info("scheduler.started",
             jobs=[j.id for j in sched.get_jobs()])
    _scheduler = sched
    return sched


# ---------------------------------------------------------------------------
# 2.2: crawl_targets 动态注册
# ---------------------------------------------------------------------------


async def _load_crawl_targets(sched: AsyncIOScheduler) -> None:
    """从 DB 拉所有 enabled crawl_targets，注册到 scheduler。"""
    from app.db.database import session_scope
    from app.services.crawl_target_service import CrawlTargetService

    try:
        async with session_scope() as s:
            targets = await CrawlTargetService(s).list_enabled()
            for t in targets:
                _register_crawl_target_job(sched, str(t.id), t.name, t.cron)
        log.info("scheduler.crawl_targets_loaded",
                 count=len(targets))
    except Exception as e:
        log.error("scheduler.crawl_targets_load_failed", error=str(e))


def _register_crawl_target_job(
    sched: AsyncIOScheduler, target_id: str, name: str, cron: str,
) -> None:
    """注册 / 更新一个 crawl_target 的 cron job。"""
    parts = cron.split()
    if len(parts) != 5:
        log.warning("scheduler.invalid_cron", target=name, cron=cron)
        return
    minute, hour, dom, month, dow = parts
    try:
        trigger = CronTrigger(
            minute=minute, hour=hour, day=dom, month=month, day_of_week=dow,
            timezone="UTC",
        )
    except ValueError as e:
        log.warning("scheduler.cron_parse_failed", target=name, error=str(e))
        return

    job_id = f"crawl_target:{target_id}"
    sched.add_job(
        lambda tid=target_id: _trigger_crawl_target(tid),
        trigger=trigger,
        id=job_id,
        replace_existing=True,
        misfire_grace_time=600,
    )
    log.info("scheduler.crawl_target_registered",
             target=name, cron=cron, job_id=job_id)


def _trigger_crawl_target(target_id: str) -> None:
    from app.workers.celery_app import celery_app

    task_id = celery_app.send_task(
        "app.workers.scheduled_crawl_worker.run_target",
        args=[target_id],
        queue="analyze_queue",
    ).id
    log.info("scheduler.dispatched",
             job=f"crawl_target:{target_id}", task_id=task_id)


def reload_crawl_target(target_id: str, name: str, cron: str) -> None:
    """外部修改 target 后调（CLI / API），刷新本目标的 schedule。

    不重启整个 scheduler；只 add_job(replace_existing=True)。
    """
    if _scheduler is None or not _scheduler.running:
        return
    _register_crawl_target_job(_scheduler, target_id, name, cron)


def unregister_crawl_target(target_id: str) -> None:
    if _scheduler is None or not _scheduler.running:
        return
    job_id = f"crawl_target:{target_id}"
    try:
        _scheduler.remove_job(job_id)
        log.info("scheduler.crawl_target_unregistered", job_id=job_id)
    except Exception:
        pass


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
