"""4.x: FallbackLLMClient 同 provider 多 model 轮换单测。

策略验证：
    1. 单 model retry 用完 → 轮换到链上下一个 model（同 provider，不算 fallback）
    2. 整条 chain 全失败 → 切跨 provider fallback
    3. 全部失败 → LLMCallError，attempts 含所有尝试
    4. 任一节点 _PermanentError → 立刻 raise（不轮换、不 fallback）
    5. 任意节点成功 → 立刻返回，剩余的 model 不被调到
    6. 向后兼容：primary 传单个 LLMClient 等价 chain 长度 1
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
    """按 call 计数从 script 取一个 outcome（'ok' / 'retriable' / 'permanent'）。"""

    def __init__(
        self,
        provider: str,
        model: str,
        *,
        script: list[str],
        is_fallback: bool = False,
    ) -> None:
        self.provider = provider
        self.model = model
        self.is_fallback = is_fallback
        self.script = list(script)
        self.calls = 0

    async def aclose(self) -> None:
        pass

    async def call(  # type: ignore[override]
        self, *, messages, system=None, tools=None, tool_choice=None,
        max_tokens=4096, temperature=0.2,
        agent_kind: AgentKind = AgentKind.OTHER, attempt_number: int = 1,
    ) -> LLMResponse:
        self.calls += 1
        if not self.script:
            outcome = "retriable"
        else:
            outcome = self.script.pop(0)

        attempt = CallAttempt(
            attempt_number=attempt_number, provider=self.provider,
            model=self.model, is_fallback=self.is_fallback,
            success=(outcome == "ok"),
            error_code=None if outcome == "ok" else f"http_{503 if outcome == 'retriable' else 400}",
            input_tokens=10, output_tokens=5, cost_usd=0.0001, latency_ms=10,
        )
        if outcome == "retriable":
            raise _RetriableError(attempt)
        if outcome == "permanent":
            raise _PermanentError(attempt)
        # ok
        return LLMResponse(
            content=[{"type": "text", "text": "ok"}],
            stop_reason="end_turn", model=self.model, provider=self.provider,
            is_fallback=self.is_fallback,
            final_attempt=attempt, all_attempts=[attempt],
        )


def _no_sleep_retry() -> RetryConfig:
    return RetryConfig(max_attempts=2, backoff_seconds=(0.0, 0.0))


@pytest.mark.asyncio
async def test_rotates_to_next_model_when_first_exhausted() -> None:
    """sonnet 全 retry 失败 → 轮到 opus；opus 第一次就成功。"""
    sonnet = _ScriptedClient("anthropic", "claude-sonnet-4-6",
                             script=["retriable"] * 2)
    opus = _ScriptedClient("anthropic", "claude-opus-4-7",
                           script=["ok"])
    haiku = _ScriptedClient("anthropic", "claude-haiku-4-5",
                            script=["ok"])
    deepseek = _ScriptedClient("deepseek", "DeepSeek-V4-Pro",
                               script=["ok"], is_fallback=True)
    client = FallbackLLMClient(
        primary=[sonnet, opus, haiku],
        fallback=deepseek,
        retry=_no_sleep_retry(),
    )
    resp = await client.call(messages=[Message(role="user", content="x")])
    assert resp.provider == "anthropic"
    assert resp.model == "claude-opus-4-7"
    assert sonnet.calls == 2   # retry 全用完
    assert opus.calls == 1     # 接力一次
    assert haiku.calls == 0    # 不再尝试
    assert deepseek.calls == 0  # provider 不切
    # all_attempts 含 sonnet 两次失败 + opus 一次成功 = 3
    assert len(resp.all_attempts) == 3


@pytest.mark.asyncio
async def test_only_switches_provider_after_full_chain_exhausted() -> None:
    """sonnet/opus/haiku 全失败 → 才切 deepseek。"""
    sonnet = _ScriptedClient("anthropic", "sonnet", script=["retriable"] * 2)
    opus = _ScriptedClient("anthropic", "opus", script=["retriable"] * 2)
    haiku = _ScriptedClient("anthropic", "haiku", script=["retriable"] * 2)
    deepseek = _ScriptedClient("deepseek", "ds", script=["ok"], is_fallback=True)
    client = FallbackLLMClient(
        primary=[sonnet, opus, haiku],
        fallback=deepseek, retry=_no_sleep_retry(),
    )
    resp = await client.call(messages=[Message(role="user", content="x")])
    assert resp.provider == "deepseek"
    assert resp.is_fallback is True
    assert sonnet.calls == 2 and opus.calls == 2 and haiku.calls == 2
    assert deepseek.calls == 1
    # 2+2+2+1 = 7 attempts
    assert len(resp.all_attempts) == 7


@pytest.mark.asyncio
async def test_permanent_error_skips_rotation_and_fallback() -> None:
    """任一模型抛 permanent → 立刻失败；不轮换，不 fallback。"""
    sonnet = _ScriptedClient("anthropic", "sonnet", script=["permanent"])
    opus = _ScriptedClient("anthropic", "opus", script=["ok"])
    deepseek = _ScriptedClient("deepseek", "ds", script=["ok"], is_fallback=True)
    client = FallbackLLMClient(
        primary=[sonnet, opus], fallback=deepseek, retry=_no_sleep_retry(),
    )
    with pytest.raises(LLMCallError, match="permanent"):
        await client.call(messages=[Message(role="user", content="x")])
    assert sonnet.calls == 1
    assert opus.calls == 0
    assert deepseek.calls == 0


@pytest.mark.asyncio
async def test_first_attempt_success_uses_no_other_clients() -> None:
    sonnet = _ScriptedClient("anthropic", "sonnet", script=["ok"])
    opus = _ScriptedClient("anthropic", "opus", script=["ok"])
    deepseek = _ScriptedClient("deepseek", "ds", script=["ok"], is_fallback=True)
    client = FallbackLLMClient(
        primary=[sonnet, opus], fallback=deepseek, retry=_no_sleep_retry(),
    )
    resp = await client.call(messages=[Message(role="user", content="x")])
    assert resp.provider == "anthropic"
    assert resp.model == "sonnet"
    assert sonnet.calls == 1
    assert opus.calls == 0
    assert deepseek.calls == 0


@pytest.mark.asyncio
async def test_chain_exhausted_no_fallback_raises() -> None:
    sonnet = _ScriptedClient("anthropic", "sonnet", script=["retriable"] * 2)
    opus = _ScriptedClient("anthropic", "opus", script=["retriable"] * 2)
    client = FallbackLLMClient(
        primary=[sonnet, opus], fallback=None, retry=_no_sleep_retry(),
    )
    with pytest.raises(LLMCallError, match="primary chain exhausted"):
        await client.call(messages=[Message(role="user", content="x")])
    assert sonnet.calls == 2
    assert opus.calls == 2


@pytest.mark.asyncio
async def test_backward_compat_single_primary() -> None:
    """旧调用：primary 传单个 LLMClient → 等价于长度 1 的 chain。"""
    sonnet = _ScriptedClient("anthropic", "sonnet", script=["retriable"] * 2)
    deepseek = _ScriptedClient("deepseek", "ds", script=["ok"], is_fallback=True)
    client = FallbackLLMClient(
        primary=sonnet,        # 单个 LLMClient，不是 list
        fallback=deepseek, retry=_no_sleep_retry(),
    )
    resp = await client.call(messages=[Message(role="user", content="x")])
    assert resp.provider == "deepseek"  # 直接切 fallback（无 model 链可轮）


@pytest.mark.asyncio
async def test_attempt_numbers_are_sequential() -> None:
    """attempt_number 必须跨整条链 + fallback 连续递增（写 llm_call_logs 时不冲突）。"""
    sonnet = _ScriptedClient("anthropic", "sonnet", script=["retriable"] * 2)
    opus = _ScriptedClient("anthropic", "opus", script=["retriable", "ok"])
    deepseek = _ScriptedClient("deepseek", "ds", script=["ok"], is_fallback=True)
    client = FallbackLLMClient(
        primary=[sonnet, opus], fallback=deepseek, retry=_no_sleep_retry(),
    )
    resp = await client.call(messages=[Message(role="user", content="x")])
    nums = [a.attempt_number for a in resp.all_attempts]
    assert nums == sorted(nums)            # 严格单调
    assert nums[0] == 1
    assert len(set(nums)) == len(nums)     # 无重复


@pytest.mark.asyncio
async def test_factory_builds_chain_from_yaml() -> None:
    """factory 必须能从 models.yaml.model_fallbacks 构出链。"""
    from app.llm.factory import build_client

    c = build_client("agent_a")
    chain_models = [x.model for x in c.primary_chain]
    # models.yaml 配的是 sonnet → opus → haiku
    assert chain_models[0] == "claude-sonnet-4-6"
    assert "claude-opus-4-7" in chain_models
    assert "claude-haiku-4-5-20251001" in chain_models
    assert all(x.provider == "anthropic" for x in c.primary_chain)
