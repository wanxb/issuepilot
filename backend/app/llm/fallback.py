"""FallbackLLMClient —— 单次 LLM 调用级混合粒度兜底。

策略（见 docs/ARCHITECTURE.md §7.1）：
    - 网络 / 限流 / 5xx 错误：按 RetryConfig 在 primary 上重试
    - primary 全部重试用完：换 fallback provider 调用一次
    - 永久性错误（401/400/422/permission）：立即失败，不重试不兜底

返回 LLMResponse.all_attempts 含所有尝试（成功 + 失败）的 CallAttempt，
caller 可一次性写入 llm_call_logs。

注意：
    - 这是单次调用级 fallback。Agent B 的 ReAct loop 中途的失败仍只 retry 同 provider，
      task 级 fallback 由 dev_worker 在更高层处理（里程碑 2.3）。
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog

from app.llm.anthropic_client import _PermanentError, _RetriableError
from app.llm.base import (
    CallAttempt,
    LLMCallError,
    LLMClient,
    LLMResponse,
    Message,
    RetryConfig,
    ToolDefinition,
)
from app.models.enums import AgentKind

log = structlog.get_logger(__name__)


class FallbackLLMClient:
    """带 retry + fallback 的 LLM 客户端 wrapper。

    本类不是 LLMClient 子类——它的 call() 签名相同，但语义更高层（含重试编排）。
    上层 Agent harness 应该用这个，而不是直接用 AnthropicClient。
    """

    def __init__(
        self,
        primary: LLMClient,
        *,
        fallback: LLMClient | None = None,
        retry: RetryConfig = RetryConfig(),
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.retry = retry

    async def aclose(self) -> None:
        await self.primary.aclose()
        if self.fallback is not None:
            await self.fallback.aclose()

    async def call(
        self,
        *,
        messages: list[Message],
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
        tool_choice: dict[str, Any] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        agent_kind: AgentKind = AgentKind.OTHER,
    ) -> LLMResponse:
        all_attempts: list[CallAttempt] = []

        # ---- 阶段 1: primary 上重试 ----
        last_retriable: _RetriableError | None = None
        for attempt_number in range(1, self.retry.max_attempts + 1):
            try:
                resp = await self.primary.call(
                    messages=messages,
                    system=system,
                    tools=tools,
                    tool_choice=tool_choice,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    agent_kind=agent_kind,
                    attempt_number=attempt_number,
                )
            except _RetriableError as e:
                last_retriable = e
                all_attempts.append(e.attempt)
                log.warning(
                    "llm.retriable",
                    attempt=attempt_number,
                    provider=self.primary.provider,
                    error_code=e.attempt.error_code,
                )
                if attempt_number < self.retry.max_attempts:
                    await asyncio.sleep(self.retry.sleep_for(attempt_number))
                continue
            except _PermanentError as e:
                all_attempts.append(e.attempt)
                log.error(
                    "llm.permanent",
                    provider=self.primary.provider,
                    error_code=e.attempt.error_code,
                )
                raise LLMCallError(
                    f"permanent error from {self.primary.provider}: {e.attempt.error_code}",
                    attempts=all_attempts,
                ) from e
            else:
                # 成功；合并 attempts 并返回
                resp.all_attempts = [*all_attempts, *resp.all_attempts]
                return resp

        # ---- 阶段 2: fallback ----
        if self.fallback is None:
            assert last_retriable is not None
            raise LLMCallError(
                f"all {self.retry.max_attempts} retries exhausted on {self.primary.provider}",
                attempts=all_attempts,
            )

        log.warning(
            "llm.fallback_switching",
            from_provider=self.primary.provider,
            to_provider=self.fallback.provider,
            primary_attempts=self.retry.max_attempts,
        )

        # fallback 调用一次（不再重试）。fallback attempt_number 从 primary 之后接着算
        fallback_attempt_no = self.retry.max_attempts + 1
        try:
            resp = await self.fallback.call(
                messages=messages,
                system=system,
                tools=tools,
                tool_choice=tool_choice,
                max_tokens=max_tokens,
                temperature=temperature,
                agent_kind=agent_kind,
                attempt_number=fallback_attempt_no,
            )
        except _RetriableError as e:
            all_attempts.append(e.attempt)
            raise LLMCallError(
                f"fallback {self.fallback.provider} also failed (retriable)",
                attempts=all_attempts,
            ) from e
        except _PermanentError as e:
            all_attempts.append(e.attempt)
            raise LLMCallError(
                f"fallback {self.fallback.provider} also failed (permanent)",
                attempts=all_attempts,
            ) from e

        # fallback 成功
        resp.all_attempts = [*all_attempts, *resp.all_attempts]
        # is_fallback 已由 fallback client 自己标记，无需覆盖
        return resp
