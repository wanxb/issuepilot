"""SQLAlchemy 2.0 异步 engine + Session 工厂。

提供：
    - engine: 单例 AsyncEngine
    - AsyncSessionLocal: AsyncSession 工厂
    - get_session(): FastAPI dependency
    - ping(): 健康检查用，执行 SELECT 1

Celery 兼容性：
    asyncpg.Connection 与创建时的 event loop 绑死；Celery 每个 task 内
    跑 `asyncio.run()` 起新 loop，会让池里的旧 connection 在 ping 时抛
    `Future attached to a different loop`。Worker 进程通过环境变量
    `DB_USE_NULL_POOL=1` 启用 NullPool（不复用连接，每次新建），
    避免跨 loop 复用。代价：worker 每次 session_scope 多一次 TCP 握手；
    可接受，因为 worker 任务以 LLM/Docker 为大头。
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

_settings = get_settings()

_USE_NULL_POOL = os.environ.get("DB_USE_NULL_POOL") == "1"

if _USE_NULL_POOL:
    # NullPool 不接受 pool_size / max_overflow / pre_ping
    engine: AsyncEngine = create_async_engine(
        _settings.database_url,
        poolclass=NullPool,
        future=True,
    )
else:
    engine = create_async_engine(
        _settings.database_url,
        pool_size=_settings.db_pool_size,
        max_overflow=_settings.db_max_overflow,
        pool_pre_ping=True,
        future=True,
    )

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    type_annotation_map: dict[Any, Any] = {}


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency：注入一个 AsyncSession，请求结束后自动关闭。"""
    async with AsyncSessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """非 FastAPI 上下文（如 Celery）用的 session context manager。"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def ping() -> bool:
    """健康检查：执行 SELECT 1，成功返回 True。"""
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        return result.scalar() == 1
