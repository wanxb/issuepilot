"""结构化日志（structlog）。

`configure_logging()` 在应用启动时调用一次。其它模块用：
    log = structlog.get_logger(__name__)
    log.info("event", key=value)
"""
from __future__ import annotations

import logging
import sys
from typing import Literal

import structlog

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


def configure_logging(level: LogLevel = "INFO", env: str = "dev") -> None:
    log_level = getattr(logging, level)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level)

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]
    if env == "dev":
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(colors=False)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
