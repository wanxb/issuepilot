"""AnthropicClient —— 基于 anthropic SDK。

支持两种认证：
    - api_key: 直连 Anthropic
    - base_url + auth_token: 中转 / 兼容 API（含 DeepSeek 的 Anthropic 兼容接口）

成本计算：
    - 优先信任 API 返回的 usage（DeepSeek 中转层也会按 Anthropic 估算给出 cost）
    - 用 model 价目表换算（详见 _estimate_cost）
"""
from __future__ import annotations

import time
from typing import Any

import anthropic
from anthropic import APIStatusError, APITimeoutError, APIConnectionError

from app.llm.base import (
    CallAttempt,
    LLMClient,
    LLMResponse,
    Message,
    ToolDefinition,
)
from app.models.enums import AgentKind


# 简化价目表（USD per 1M tokens），用于无 cost 字段时估算
# 数据基于公开价格，仅用于内部成本观测；价格变动直接改这里即可
_PRICING_PER_MILLION: dict[str, tuple[float, float]] = {
    # model_substring : (input_per_M, output_per_M)
    "claude-opus-4-7": (15.0, 75.0),
    "claude-opus-4-6": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    # 2.5: DeepSeek 通过 Anthropic 兼容接口走同一 client；价格 USD per 1M
    # 来自 deepseek.com 公开定价（DeepSeek-V4-Pro non-cache hit input/output）
    "DeepSeek-V4-Pro": (0.27, 1.10),
    "DeepSeek-V3": (0.27, 1.10),
    "deepseek-chat": (0.27, 1.10),     # OpenAI 兼容接口的模型名
    "deepseek-reasoner": (0.55, 2.19),
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """子串匹配。命中即返回，不命中返 0.0 + 记 warn 给运维。"""
    for key, (in_price, out_price) in _PRICING_PER_MILLION.items():
        if key in model:
            return (input_tokens * in_price + output_tokens * out_price) / 1_000_000
    return 0.0


class AnthropicClient(LLMClient):
    def __init__(
        self,
        model: str,
        *,
        provider_label: str = "anthropic",
        api_key: str | None = None,
        base_url: str | None = None,
        auth_token: str | None = None,
        is_fallback: bool = False,
        timeout: float = 120.0,
    ) -> None:
        kwargs: dict[str, Any] = {"timeout": timeout}
        if base_url:
            kwargs["base_url"] = base_url
        if auth_token:
            kwargs["auth_token"] = auth_token
        if api_key:
            kwargs["api_key"] = api_key

        self._client = anthropic.AsyncAnthropic(**kwargs)
        self.model = model
        self.provider = provider_label
        self.is_fallback = is_fallback

    async def aclose(self) -> None:
        await self._client.close()

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
        attempt_number: int = 1,
    ) -> LLMResponse:
        start = time.perf_counter()
        sdk_messages = [_message_to_sdk(m) for m in messages]
        sdk_kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": sdk_messages,
        }
        if system:
            sdk_kwargs["system"] = system
        if tools:
            sdk_kwargs["tools"] = [t.to_anthropic() for t in tools]
        if tool_choice:
            sdk_kwargs["tool_choice"] = tool_choice

        try:
            sdk_resp = await self._client.messages.create(**sdk_kwargs)
        except APITimeoutError as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            attempt = CallAttempt(
                attempt_number=attempt_number,
                provider=self.provider,
                model=self.model,
                is_fallback=self.is_fallback,
                success=False,
                error_code="timeout",
                error_detail=str(e)[:1000],
                latency_ms=elapsed_ms,
            )
            raise _RetriableError(attempt) from e
        except APIConnectionError as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            attempt = CallAttempt(
                attempt_number=attempt_number,
                provider=self.provider,
                model=self.model,
                is_fallback=self.is_fallback,
                success=False,
                error_code="connection",
                error_detail=str(e)[:1000],
                latency_ms=elapsed_ms,
            )
            raise _RetriableError(attempt) from e
        except APIStatusError as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            status_code = getattr(e, "status_code", 0) or 0
            error_code = f"http_{status_code}"
            attempt = CallAttempt(
                attempt_number=attempt_number,
                provider=self.provider,
                model=self.model,
                is_fallback=self.is_fallback,
                success=False,
                error_code=error_code,
                error_detail=str(e)[:1000],
                latency_ms=elapsed_ms,
            )
            if status_code in (408, 425, 429, 500, 502, 503, 504):
                raise _RetriableError(attempt) from e
            raise _PermanentError(attempt) from e

        # 成功
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        usage = getattr(sdk_resp, "usage", None)
        in_tokens = getattr(usage, "input_tokens", 0) or 0
        out_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cost = _estimate_cost(self.model, in_tokens + cache_read + cache_creation, out_tokens)

        content_blocks: list[dict[str, Any]] = []
        for block in sdk_resp.content:
            data = block.model_dump() if hasattr(block, "model_dump") else dict(block)  # type: ignore[arg-type]
            content_blocks.append(data)

        attempt = CallAttempt(
            attempt_number=attempt_number,
            provider=self.provider,
            model=self.model,
            is_fallback=self.is_fallback,
            success=True,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
            cost_usd=cost,
            latency_ms=elapsed_ms,
        )

        return LLMResponse(
            content=content_blocks,
            stop_reason=str(sdk_resp.stop_reason or ""),
            model=self.model,
            provider=self.provider,
            is_fallback=self.is_fallback,
            final_attempt=attempt,
            all_attempts=[attempt],
        )


def _message_to_sdk(m: Message) -> dict[str, Any]:
    if isinstance(m.content, str):
        return {"role": m.role, "content": m.content}
    return {"role": m.role, "content": m.content}


# ---------------------------------------------------------------------------
# 内部错误类型（被 FallbackLLMClient 捕获后转换为 CallAttempt 列表）
# ---------------------------------------------------------------------------


class _RetriableError(Exception):
    def __init__(self, attempt: CallAttempt) -> None:
        super().__init__(attempt.error_code or "retriable")
        self.attempt = attempt


class _PermanentError(Exception):
    def __init__(self, attempt: CallAttempt) -> None:
        super().__init__(attempt.error_code or "permanent")
        self.attempt = attempt
