"""LLMCallAttempt → llm_call_logs 表持久化。

调用方拿到 LLMResponse 后调一次 persist_attempts()，本模块负责把
每个 CallAttempt 转成 LLMCallLog 行批量插入。

设计：
    - 接受 AsyncSession（Service 层注入），不自己开 session
    - 失败时不抛异常，只 log.error——避免日志写入失败影响业务流程
"""
from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.base import CallAttempt
from app.models.enums import AgentKind
from app.models.llm_call_log import LLMCallLog

log = structlog.get_logger(__name__)


async def persist_attempts(
    session: AsyncSession,
    attempts: list[CallAttempt],
    *,
    agent_kind: AgentKind,
    issue_id: uuid.UUID | None = None,
    prompt_version: str | None = None,
) -> None:
    """把 LLMResponse.all_attempts 批量写入 llm_call_logs。

    本函数 best-effort：
        - 写入异常 → 记 structlog 不重抛
        - 不在内部 commit；caller 控制事务边界
    """
    if not attempts:
        return

    rows = [
        LLMCallLog(
            issue_id=issue_id,
            agent_kind=agent_kind,
            provider=a.provider,
            model=a.model,
            is_fallback=a.is_fallback,
            success=a.success,
            error_code=a.error_code,
            error_detail=a.error_detail,
            input_tokens=a.input_tokens,
            output_tokens=a.output_tokens,
            cache_read_tokens=a.cache_read_tokens,
            cache_creation_tokens=a.cache_creation_tokens,
            cost_usd=a.cost_usd,
            latency_ms=a.latency_ms,
            attempt_number=a.attempt_number,
            prompt_version=prompt_version,
        )
        for a in attempts
    ]
    try:
        session.add_all(rows)
        await session.flush()
    except Exception as exc:
        log.error(
            "llm_call_logs.persist_failed",
            error=str(exc),
            attempts_count=len(attempts),
            agent_kind=agent_kind.value,
        )
