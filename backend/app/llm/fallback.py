"""FallbackLLMClient —— 单次 LLM 调用级混合粒度兜底。

策略（4.x 升级版）：
    primary_chain = [model_A, model_B, ..., model_N]   # 同 provider，多 model
    cross_provider_fallback                            # 不同 provider，单 model

    1. 对 primary_chain 逐个 model 调用：
         每个 model 内部按 RetryConfig 重试（网络 / 限流 / 5xx）
         该 model 全部 retry 失败 → 不切换 provider，先切下个 model
    2. primary_chain 全部 model 都失败 → 切换到 cross_provider_fallback（一次）
    3. 永久性错误（401/400/422）：立即失败，不轮换、不兜底

返回 LLMResponse.all_attempts 含所有尝试（成功 + 失败）的 CallAttempt，
caller 可一次性写入 llm_call_logs。

注意：
    - is_fallback 在 attempt 层级：同 provider 内 model rotation 仍 is_fallback=False；
      只有跨 provider 才标 is_fallback=True
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
    """带 retry + model rotation + 跨 provider fallback 的 LLM 客户端 wrapper。

    本类不是 LLMClient 子类——它的 call() 签名相同，但语义更高层（含重试编排）。
    上层 Agent harness 应该用这个，而不是直接用 AnthropicClient。
    """

    def __init__(
        self,
        primary: LLMClient | list[LLMClient],
        *,
        fallback: LLMClient | None = None,
        retry: RetryConfig = RetryConfig(),
    ) -> None:
        # 向后兼容：旧代码传单个 LLMClient → 等价 chain 长度 1
        self.primary_chain: list[LLMClient] = (
            [primary] if isinstance(primary, LLMClient) else list(primary)
        )
        if not self.primary_chain:
            raise ValueError("primary chain must contain at least one LLMClient")
        # 向后兼容属性：上层有的代码读 self.primary
        self.primary = self.primary_chain[0]
        self.fallback = fallback
        self.retry = retry

    async def aclose(self) -> None:
        for c in self.primary_chain:
            try:
                await c.aclose()
            except Exception:
                pass
        if self.fallback is not None:
            try:
                await self.fallback.aclose()
            except Exception:
                pass

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
        attempt_number = 0   # 跨整个 call 全局递增，落到 llm_call_logs

        # ---- 阶段 1: primary chain 上逐 model retry ----
        last_retriable: _RetriableError | None = None
        for chain_idx, client in enumerate(self.primary_chain):
            client_exhausted = False
            for retry_i in range(1, self.retry.max_attempts + 1):
                attempt_number += 1
                try:
                    resp = await client.call(
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
                        chain_idx=chain_idx,
                        provider=client.provider,
                        model=client.model,
                        error_code=e.attempt.error_code,
                    )
                    if retry_i < self.retry.max_attempts:
                        await asyncio.sleep(self.retry.sleep_for(retry_i))
                    continue
                except _PermanentError as e:
                    all_attempts.append(e.attempt)
                    log.error(
                        "llm.permanent",
                        provider=client.provider,
                        model=client.model,
                        error_code=e.attempt.error_code,
                    )
                    raise LLMCallError(
                        f"permanent error from {client.provider}/{client.model}: "
                        f"{e.attempt.error_code}",
                        attempts=all_attempts,
                    ) from e
                else:
                    # 成功
                    resp.all_attempts = [*all_attempts, *resp.all_attempts]
                    return resp
            client_exhausted = True

            # 该 model 全部 retry 用完了；如果链上还有下一个 model → 轮换
            if client_exhausted and chain_idx < len(self.primary_chain) - 1:
                next_client = self.primary_chain[chain_idx + 1]
                log.warning(
                    "llm.model_rotation",
                    from_model=client.model,
                    to_model=next_client.model,
                    provider=client.provider,
                    retries_exhausted=self.retry.max_attempts,
                )

        # ---- 阶段 2: 跨 provider fallback ----
        if self.fallback is None:
            assert last_retriable is not None
            chain_summary = " → ".join(c.model for c in self.primary_chain)
            raise LLMCallError(
                f"primary chain exhausted ({chain_summary}) on "
                f"{self.primary_chain[0].provider}, no fallback configured",
                attempts=all_attempts,
            )

        log.warning(
            "llm.fallback_switching",
            from_provider=self.primary_chain[0].provider,
            to_provider=self.fallback.provider,
            chain_length=len(self.primary_chain),
            primary_attempts=self.retry.max_attempts * len(self.primary_chain),
        )

        attempt_number += 1
        try:
            resp = await self.fallback.call(
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

        resp.all_attempts = [*all_attempts, *resp.all_attempts]
        return resp
