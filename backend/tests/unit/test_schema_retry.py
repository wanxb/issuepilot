"""Schema 校验失败重试（2.3）—— harness 公共逻辑。

测目标：call_with_schema_retry 在第 1 次 schema 失败时会再调一次 LLM，
第 2 次返回合法则成功；两次仍失败则抛 SchemaValidationError。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.agents._schema_retry import (
    SchemaValidationError,
    ToolNotCalledError,
    call_with_schema_retry,
)
from app.agents.schemas import AgentAOutput
from app.agents.tools import EVALUATE_ISSUE_TOOL
from app.llm.base import CallAttempt, LLMClient, LLMResponse, Message
from app.llm.fallback import FallbackLLMClient
from app.models.enums import AgentKind


VALID_AGENT_A: dict[str, Any] = {
    "total_score": 7.0,
    "difficulty": "medium",
    "estimated_hours": 1.5,
    "summary": "x" * 60,
    "recommendation": "y" * 10,
    "is_worth_developing": True,
    "dimensions": {
        "clarity": {"score": 7, "comment": "ok"},
        "feasibility": {"score": 7, "comment": "ok"},
        "value": {"score": 7, "comment": "ok"},
        "repo_activity": {"score": 7, "comment": "ok"},
        "context_sufficiency": {"score": 7, "comment": "ok"},
    },
}


class _ScriptedLLM(LLMClient):
    """按 call 顺序依次返回预定义的 content。"""

    def __init__(self, scripts: list[tuple[list[dict[str, Any]], str]]) -> None:
        self.provider = "anthropic"
        self.model = "claude-sonnet-4-6"
        self.is_fallback = False
        self._scripts = list(scripts)
        self.call_count = 0
        self.last_messages: list[Message] = []

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
        self.call_count += 1
        self.last_messages = list(messages)
        content, stop = self._scripts.pop(0)
        attempt = CallAttempt(
            attempt_number=self.call_count, provider=self.provider,
            model=self.model, is_fallback=False, success=True,
            input_tokens=100 + self.call_count * 50,  # 不同次不同 token
            output_tokens=50, cost_usd=0.001, latency_ms=500,
        )
        return LLMResponse(
            content=content, stop_reason=stop, model=self.model,
            provider=self.provider, is_fallback=False,
            final_attempt=attempt, all_attempts=[attempt],
        )


def _tool_use(input_: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "tool_use", "id": "t",
        "name": EVALUATE_ISSUE_TOOL.name,
        "input": input_,
    }


@pytest.mark.asyncio
async def test_first_try_valid_no_retry() -> None:
    llm = _ScriptedLLM([([_tool_use(VALID_AGENT_A)], "tool_use")])
    out, resp = await call_with_schema_retry(
        llm=FallbackLLMClient(llm), user_msg="go",
        system="sys", tool=EVALUATE_ISSUE_TOOL,
        output_cls=AgentAOutput, agent_kind=AgentKind.AGENT_A,
        agent_label="agent_a", max_tokens=2048, temperature=0.2,
    )
    assert out.total_score == 7.0
    assert llm.call_count == 1
    assert len(resp.all_attempts) == 1


@pytest.mark.asyncio
async def test_first_invalid_second_valid_retries_once() -> None:
    bad = {**VALID_AGENT_A, "total_score": 99}  # >10 → schema fail
    llm = _ScriptedLLM([
        ([_tool_use(bad)], "tool_use"),
        ([_tool_use(VALID_AGENT_A)], "tool_use"),
    ])
    out, resp = await call_with_schema_retry(
        llm=FallbackLLMClient(llm), user_msg="go",
        system="sys", tool=EVALUATE_ISSUE_TOOL,
        output_cls=AgentAOutput, agent_kind=AgentKind.AGENT_A,
        agent_label="agent_a", max_tokens=2048, temperature=0.2,
    )
    assert out.total_score == 7.0
    assert llm.call_count == 2
    # all_attempts 合并两次（每次 1 个）
    assert len(resp.all_attempts) == 2
    # 第 2 次 messages 应该带有 correction hint
    contents = [m.content for m in llm.last_messages]
    assert any("failed JSON schema validation" in c for c in contents)


@pytest.mark.asyncio
async def test_both_invalid_raises_after_two_tries() -> None:
    bad = {**VALID_AGENT_A, "total_score": 99}
    llm = _ScriptedLLM([
        ([_tool_use(bad)], "tool_use"),
        ([_tool_use(bad)], "tool_use"),
    ])
    with pytest.raises(SchemaValidationError):
        await call_with_schema_retry(
            llm=FallbackLLMClient(llm), user_msg="go",
            system="sys", tool=EVALUATE_ISSUE_TOOL,
            output_cls=AgentAOutput, agent_kind=AgentKind.AGENT_A,
            agent_label="agent_a", max_tokens=2048, temperature=0.2,
        )
    assert llm.call_count == 2  # 用尽 2 次


@pytest.mark.asyncio
async def test_tool_not_called_no_retry() -> None:
    """模型没调工具 → 立刻判失败，不耗费第 2 次 budget。"""
    llm = _ScriptedLLM([([{"type": "text", "text": "nope"}], "end_turn")])
    with pytest.raises(ToolNotCalledError):
        await call_with_schema_retry(
            llm=FallbackLLMClient(llm), user_msg="go",
            system="sys", tool=EVALUATE_ISSUE_TOOL,
            output_cls=AgentAOutput, agent_kind=AgentKind.AGENT_A,
            agent_label="agent_a", max_tokens=2048, temperature=0.2,
        )
    assert llm.call_count == 1
