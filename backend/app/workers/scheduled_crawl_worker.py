"""scheduled_crawl_worker — APScheduler 触发的定时抓取任务（2.2）。

scheduler 在 API 容器 send_task；本任务跑在 worker 容器：
    1. 加载 CrawlTarget
    2. 校验 enabled（防止 race condition：scheduler 已派发但 target 被 disable）
    3. 调 CrawlerService.from_target
    4. 写回 last_run_at / last_status

复用 analyze_queue（不为低频定时任务单开 queue）。
"""
from __future__ import annotations

import asyncio
import uuid

import structlog
from celery import Task

from app.db.database import session_scope
from app.github.client import GitHubClient
from app.services.crawl_target_service import (
    CrawlTargetNotFound,
    CrawlTargetService,
)
from app.services.crawler_service import CrawlerService
from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)


@celery_app.task(
    name="app.workers.scheduled_crawl_worker.run_target",
    queue="analyze_queue",
    acks_late=True,
    max_retries=0,
)
def run_target(target_id: str) -> dict[str, object]:
    return asyncio.run(_run_target_async(uuid.UUID(target_id)))


async def _run_target_async(target_id: uuid.UUID) -> dict[str, object]:
    # Phase 1：加载 + 校验
    async with session_scope() as s:
        svc = CrawlTargetService(s)
        try:
            target = await svc.get(target_id)
        except CrawlTargetNotFound:
            log.warning("scheduled_crawl.target_missing", target_id=str(target_id))
            return {"error": "target_missing"}
        if not target.enabled:
            log.info("scheduled_crawl.target_disabled", target_id=str(target_id))
            return {"skipped": True, "reason": "disabled"}
        snapshot = {
            "name": target.name,
            "source": target.source,
            "spec": dict(target.spec or {}),
        }

    # Phase 2：抓取（GitHubClient 自带 token）
    pending_analyze_ids: list[uuid.UUID] = []
    outcome = None
    async with GitHubClient.from_settings() as gh:
        async with session_scope() as s:
            crawler = CrawlerService(s, gh)
            try:
                outcome = await crawler.from_target(
                    target_id=target_id,
                    target_name=snapshot["name"],
                    source=snapshot["source"],
                    spec=snapshot["spec"],
                )
                status_str = (
                    "succeeded" if outcome.repos_failed == 0 and outcome.repos_attempted > 0
                    else "partial" if outcome.repos_succeeded > 0
                    else "failed"
                )
                err = (
                    "; ".join(f"{k}:{v}" for k, v in
                              list(outcome.failures.items())[:3])
                    if outcome.failures else None
                )
                pending_analyze_ids = list(outcome.pending_analyze_ids)
            except Exception as e:
                status_str = "failed"
                err = str(e)[:300]
                log.error(
                    "scheduled_crawl.from_target_failed",
                    target_id=str(target_id), error=err,
                )
                raise
            finally:
                # 即使抛了也要写一次 last_run_at；同 session 写入 outcome
                await CrawlTargetService(s).update_run_result(
                    target_id, status=status_str, error=err,
                )

    # Phase 3: commit 后入队 analyze_issue（修 race：worker 比 outer commit 更快读到 NOT FOUND）
    if pending_analyze_ids:
        for iid in pending_analyze_ids:
            try:
                celery_app.send_task(
                    "app.workers.analyze_worker.analyze_issue",
                    args=[str(iid)], queue="analyze_queue",
                )
            except Exception as e:
                log.warning(
                    "scheduled_crawl.enqueue_failed",
                    issue_id=str(iid), error=str(e),
                )

    log.info(
        "scheduled_crawl.done",
        target_id=str(target_id), status=status_str,
        repos_ok=outcome.repos_succeeded if outcome else 0,
        issues_enqueued=outcome.issues_enqueued if outcome else 0,
        analyze_dispatched=len(pending_analyze_ids),
    )
    return {
        "target_id": str(target_id),
        "status": status_str,
        "repos_succeeded": outcome.repos_succeeded if outcome else 0,
        "issues_enqueued": outcome.issues_enqueued if outcome else 0,
    }
