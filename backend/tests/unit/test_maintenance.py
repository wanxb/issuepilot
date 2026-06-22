"""maintenance_worker（2.3）—— 单元层校验。

DB / GitHub API 交互在 e2e 验证（host 端 curl + docker exec psql），本文件只测：
    - revert 标题正则：REVERT_TITLE_RE 命中常见格式
    - 模块导入 + 任务注册（不抛）
    - scheduler init 幂等
"""
from __future__ import annotations

import pytest

from app.workers.maintenance_worker import REVERT_TITLE_RE


class TestRevertTitleRegex:
    @pytest.mark.parametrize("title", [
        "Revert \"fix: typo\"",
        "Revert: bad commit",
        "revert thing",
        "REVERT: rolled back",
        "Revert \"feat(x): y\" (#42)",
    ])
    def test_matches_common_formats(self, title: str) -> None:
        assert REVERT_TITLE_RE.match(title) is not None

    @pytest.mark.parametrize("title", [
        "fix: revert later",            # revert 不在开头
        "Add revert helper",
        "",
        "  Revert ...",                 # 前导空格不算（GitHub 不会这样）
    ])
    def test_no_false_positives(self, title: str) -> None:
        assert REVERT_TITLE_RE.match(title) is None


class TestTasksRegistered:
    """maintenance_worker 模块导入即注册 Celery 任务；不抛就是 ok。"""

    def test_imports(self) -> None:
        from app.workers import maintenance_worker  # noqa: F401
        from app.workers.celery_app import celery_app

        assert "app.workers.maintenance_worker.stale_archive_scan" in celery_app.tasks
        assert "app.workers.maintenance_worker.revert_scan" in celery_app.tasks


class TestScheduler:
    """scheduler 模块导入 + init 幂等（AsyncIOScheduler 需要 running loop）。"""

    @pytest.mark.asyncio
    async def test_init_idempotent(self) -> None:
        from app.scheduler import init_scheduler, shutdown_scheduler

        s1 = init_scheduler()
        s2 = init_scheduler()
        try:
            assert s1 is s2  # 二次调用返回同一个 scheduler
            assert s1.running
            ids = {j.id for j in s1.get_jobs()}
            assert {"stale_archive_scan", "revert_scan"} <= ids
        finally:
            shutdown_scheduler()
