"""FallbackLLMClient 单元测试。

不调用真实 LLM——用假 LLMClient 实现可控的成功/失败序列。
覆盖：
    - primary 一次成功
    - primary 重试 N 次后成功
    - primary 全部失败 → 切 fallback → 成功
    - primary 全部失败 + 无 fallback → LLMCallError
    - primary 全部失败 + fallback 也失败 → LLMCallError
    - primary 永久性错误 → 立即抛 LLMCallError 不尝试 fallback
    - 所有 attempts 都被收集到 LLMResponse.all_attempts
"""
from __future__ import annotations

from typing import Any

import pytest

from app.llm.anthropic_client import _PermanentError, _RetriableError
from app.llm.base import (
    CallAttempt,
    LLMCallError,
    LLMClient,
    LLMResponse,
    Message,
    RetryConfig,
)
from app.llm.fallback import FallbackLLMClient
from app.models.enums import AgentKind


class _ScriptedClient(LLMClient):
    """按预设脚本依次返回成功/失败的 fake client。"""

    def __init__(
        self,
        provider: str,
        model: str,
        script: list[str],   # "ok" | "retriable" | "permanent"
        *,
        is_fallback: bool = False,
    ) -> None:
        self.provider = provider
        self.model = model
        self.is_fallback = is_fallback
        self._script = list(script)
        self.calls_made = 0

    async def aclose(self) -> None:
        pass

    async def call(  # type: ignore[override]
        self,
        *,
        messages: list[Message],
        system: str | None = None,
        tools: Any = None,
        tool_choice: Any = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        agent_kind: AgentKind = AgentKind.OTHER,
        attempt_number: int = 1,
    ) -> LLMResponse:
        self.calls_made += 1
        if not self._script:
            outcome = "ok"
        else:
            outcome = self._script.pop(0)

        if outcome == "retriable":
            attempt = CallAttempt(
                attempt_number=attempt_number,
                provider=self.provider,
                model=self.model,
                is_fallback=self.is_fallback,
                success=False,
                error_code="http_503",
                latency_ms=100,
            )
            raise _RetriableError(attempt)
        if outcome == "permanent":
            attempt = CallAttempt(
                attempt_number=attempt_number,
                provider=self.provider,
                model=self.model,
                is_fallback=self.is_fallback,
                success=False,
                error_code="http_400",
                latency_ms=50,
            )
            raise _PermanentError(attempt)

        attempt = CallAttempt(
            attempt_number=attempt_number,
            provider=self.provider,
            model=self.model,
            is_fallback=self.is_fallback,
            success=True,
            input_tokens=100,
            output_tokens=20,
            cost_usd=0.001,
            latency_ms=500,
        )
        return LLMResponse(
            content=[{"type": "text", "text": "ok"}],
            stop_reason="end_turn",
            model=self.model,
            provider=self.provider,
            is_fallback=self.is_fallback,
            final_attempt=attempt,
            all_attempts=[attempt],
        )


@pytest.fixture
def fast_retry() -> RetryConfig:
    """0 sleep, 3 attempts — keep tests fast."""
    return RetryConfig(max_attempts=3, backoff_seconds=(0.0,))


@pytest.fixture
def messages() -> list[Message]:
    return [Message(role="user", content="hi")]


class TestSuccessPaths:
    @pytest.mark.asyncio
    async def test_primary_first_attempt_succeeds(
        self, fast_retry: RetryConfig, messages: list[Message],
    ) -> None:
        primary = _ScriptedClient("anthropic", "claude-sonnet-4-6", ["ok"])
        client = FallbackLLMClient(primary, retry=fast_retry)
        resp = await client.call(messages=messages)

        assert resp.provider == "anthropic"
        assert resp.is_fallback is False
        assert len(resp.all_attempts) == 1
        assert resp.all_attempts[0].success is True
        assert primary.calls_made == 1

    @pytest.mark.asyncio
    async def test_primary_succeeds_after_retries(
        self, fast_retry: RetryConfig, messages: list[Message],
    ) -> None:
        primary = _ScriptedClient(
            "anthropic", "claude-sonnet-4-6",
            ["retriable", "retriable", "ok"],
        )
        client = FallbackLLMClient(primary, retry=fast_retry)
        resp = await client.call(messages=messages)

        assert primary.calls_made == 3
        assert len(resp.all_attempts) == 3
        assert [a.success for a in resp.all_attempts] == [False, False, True]
        assert resp.is_fallback is False


class TestFallbackPaths:
    @pytest.mark.asyncio
    async def test_primary_exhausted_falls_back(
        self, fast_retry: RetryConfig, messages: list[Message],
    ) -> None:
        primary = _ScriptedClient(
            "anthropic", "claude-sonnet-4-6",
            ["retriable", "retriable", "retriable"],
        )
        fallback = _ScriptedClient(
            "deepseek", "DeepSeek-V4-Pro", ["ok"], is_fallback=True,
        )
        client = FallbackLLMClient(primary, fallback=fallback, retry=fast_retry)
        resp = await client.call(messages=messages)

        assert primary.calls_made == 3
        assert fallback.calls_made == 1
        assert resp.is_fallback is True
        assert resp.provider == "deepseek"
        assert len(resp.all_attempts) == 4
        assert resp.all_attempts[3].is_fallback is True
        assert resp.all_attempts[3].attempt_number == 4

    @pytest.mark.asyncio
    async def test_primary_and_fallback_fail(
        self, fast_retry: RetryConfig, messages: list[Message],
    ) -> None:
        primary = _ScriptedClient(
            "anthropic", "claude-sonnet-4-6", ["retriable"] * 3,
        )
        fallback = _ScriptedClient(
            "deepseek", "DeepSeek-V4-Pro", ["retriable"], is_fallback=True,
        )
        client = FallbackLLMClient(primary, fallback=fallback, retry=fast_retry)
        with pytest.raises(LLMCallError) as exc:
            await client.call(messages=messages)
        assert len(exc.value.attempts) == 4
        assert exc.value.attempts[-1].is_fallback is True

    @pytest.mark.asyncio
    async def test_no_fallback_configured(
        self, fast_retry: RetryConfig, messages: list[Message],
    ) -> None:
        primary = _ScriptedClient("anthropic", "x", ["retriable"] * 3)
        client = FallbackLLMClient(primary, retry=fast_retry)
        with pytest.raises(LLMCallError) as exc:
            await client.call(messages=messages)
        assert len(exc.value.attempts) == 3


class TestPermanentErrors:
    @pytest.mark.asyncio
    async def test_permanent_does_not_retry_or_fallback(
        self, fast_retry: RetryConfig, messages: list[Message],
    ) -> None:
        primary = _ScriptedClient("anthropic", "x", ["permanent"])
        fallback = _ScriptedClient("deepseek", "y", ["ok"], is_fallback=True)
        client = FallbackLLMClient(primary, fallback=fallback, retry=fast_retry)
        with pytest.raises(LLMCallError):
            await client.call(messages=messages)
        assert primary.calls_made == 1
        assert fallback.calls_made == 0   # 永久错误不触发兜底
