"""Celery 应用 + 四队列声明。

队列：
    - analyze_queue: Agent A 评估
    - dev_queue:     Agent B 开发
    - review_queue:  Agent C 评审
    - profile_queue: Agent D 仓库画像（1.5c 新增，per-repo 异步生成）

里程碑 1.1 只接通 broker，提供 ping 任务做冒烟。
后续里程碑在 analyze_worker.py / dev_worker.py / review_worker.py /
profile_worker.py 注册任务。
"""
from __future__ import annotations

from celery import Celery
from kombu import Queue

from app.core.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "issuepilot",
    broker=_settings.celery_broker_url,
    backend=_settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue="analyze_queue",
    task_queues=(
        Queue("analyze_queue"),
        Queue("dev_queue"),
        Queue("review_queue"),
        Queue("profile_queue"),
        Queue("classify_queue"),
    ),
    task_routes={
        "app.workers.analyze_worker.*": {"queue": "analyze_queue"},
        "app.workers.dev_worker.*": {"queue": "dev_queue"},
        "app.workers.review_worker.*": {"queue": "review_queue"},
        "app.workers.profile_worker.*": {"queue": "profile_queue"},
        "app.workers.classify_worker.*": {"queue": "classify_queue"},
    },
    # 1.1 冒烟用：单独路由 ping 任务到 analyze_queue
    task_default_exchange="issuepilot",
)


@celery_app.task(name="app.workers.celery_app.ping")
def ping() -> str:
    """冒烟任务：返回 'pong'。"""
    return "pong"


# 显式 import 所有 worker 模块，让 @celery_app.task 装饰器在 import 时注册任务
# （autodiscover_tasks 是为 package/tasks 约定准备的，本项目不用那个约定）
from app.workers import analyze_worker  # noqa: E402, F401
from app.workers import classify_worker  # noqa: E402, F401
from app.workers import dev_worker      # noqa: E402, F401
from app.workers import maintenance_worker  # noqa: E402, F401
from app.workers import profile_worker  # noqa: E402, F401
from app.workers import review_worker   # noqa: E402, F401
from app.workers import scheduled_crawl_worker  # noqa: E402, F401
