"""ORM 模型聚合 import。

在此显式 import 每个模型类，让 alembic autogenerate 通过 Base.metadata 感知到。
新增模型时在这里 + alembic/env.py 各加一行 import。
"""
from app.models.crawl_job import CrawlJob
from app.models.evaluation import Evaluation
from app.models.issue import Issue
from app.models.llm_call_log import LLMCallLog
from app.models.repository import Repository

__all__ = [
    "CrawlJob",
    "Evaluation",
    "Issue",
    "LLMCallLog",
    "Repository",
]
