"""Agent A schema + harness 测试（不调真实 LLM）。"""
from __future__ import annotations

from typing import Any

import pytest

from app.agents.agent_a import (
    AgentA,
    SchemaValidationError,
    ToolNotCalledError,
)
from app.agents.schemas import AgentAInput, AgentAOutput
from app.agents.tools import EVALUATE_ISSUE_TOOL
from app.llm.base import (
    CallAttempt,
    LLMClient,
    LLMResponse,
    Message,
)
from app.llm.fallback import FallbackLLMClient
from app.models.enums import AgentKind


# ---------------------------------------------------------------------------
# Schema 测试
# ---------------------------------------------------------------------------


VALID_TOOL_INPUT: dict[str, Any] = {
    "total_score": 7.5,
    "difficulty": "medium",
    "estimated_hours": 2.0,
    "summary": "subtract 函数返回错误，单测一行可修复。",
    "recommendation": "建议开发",
    "is_worth_developing": True,
    "dimensions": {
        "clarity":             {"score": 8.5, "comment": "复现步骤完整"},
        "feasibility":         {"score": 8.0, "comment": "改一个文件即可"},
        "value":               {"score": 7.0, "comment": "影响常见场景"},
        "repo_activity":       {"score": 8.0, "comment": "近 7 天有 merge"},
        "context_sufficiency": {"score": 6.0, "comment": "缺 CONTRIBUTING"},
    },
}


class TestAgentAOutputSchema:
    def test_valid(self) -> None:
        AgentAOutput.model_validate(VALID_TOOL_INPUT)

    def test_missing_dimension_rejected(self) -> None:
        bad = {**VALID_TOOL_INPUT, "dimensions": {"clarity": {"score": 5, "comment": "ok"}}}
        with pytest.raises(Exception):
            AgentAOutput.model_validate(bad)

    def test_out_of_range_score_rejected(self) -> None:
        bad = {**VALID_TOOL_INPUT, "total_score": 11.0}
        with pytest.raises(Exception):
            AgentAOutput.model_validate(bad)

    def test_invalid_difficulty_rejected(self) -> None:
        bad = {**VALID_TOOL_INPUT, "difficulty": "trivial"}
        with pytest.raises(Exception):
            AgentAOutput.model_validate(bad)

    def test_empty_comment_rejected(self) -> None:
        bad = {**VALID_TOOL_INPUT}
        bad["dimensions"] = {**bad["dimensions"],
                             "clarity": {"score": 5, "comment": "   "}}
        with pytest.raises(Exception):
            AgentAOutput.model_validate(bad)


# ---------------------------------------------------------------------------
# Harness 测试
# ---------------------------------------------------------------------------


def _make_input() -> AgentAInput:
    return AgentAInput(
        issue_title="bug: subtract wrong",
        issue_body="subtract(5,3) returns 8",
        issue_labels=["bug"],
        issue_url="https://github.com/o/r/issues/1",
        repo_full_name="o/r",
        repo_language="Python",
        repo_stars=100,
    )


class _FakeLLM(LLMClient):
    def __init__(self, content: list[dict[str, Any]], stop_reason: str = "tool_use") -> None:
        self.provider = "anthropic"
        self.model = "claude-sonnet-4-6"
        self.is_fallback = False
        self._content = content
        self._stop = stop_reason

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
        attempt = CallAttempt(
            attempt_number=1, provider=self.provider, model=self.model,
            is_fallback=False, success=True, input_tokens=500,
            output_tokens=200, cost_usd=0.01, latency_ms=2000,
        )
        return LLMResponse(
            content=self._content,
            stop_reason=self._stop,
            model=self.model,
            provider=self.provider,
            is_fallback=False,
            final_attempt=attempt,
            all_attempts=[attempt],
        )


@pytest.mark.asyncio
async def test_harness_happy_path() -> None:
    fake = _FakeLLM(
        content=[
            {"type": "text", "text": "I will evaluate."},
            {
                "type": "tool_use",
                "id": "toolu_x",
                "name": EVALUATE_ISSUE_TOOL.name,
                "input": VALID_TOOL_INPUT,
            },
        ],
    )
    harness = AgentA(FallbackLLMClient(fake))
    output, resp = await harness.analyze(_make_input())
    assert output.total_score == 7.5
    assert output.is_worth_developing is True
    assert resp.provider == "anthropic"


@pytest.mark.asyncio
async def test_harness_rejects_tool_not_called() -> None:
    fake = _FakeLLM(
        content=[{"type": "text", "text": "I refuse."}],
        stop_reason="end_turn",
    )
    harness = AgentA(FallbackLLMClient(fake))
    with pytest.raises(ToolNotCalledError):
        await harness.analyze(_make_input())


@pytest.mark.asyncio
async def test_harness_rejects_invalid_schema() -> None:
    bad_input = {**VALID_TOOL_INPUT, "total_score": -1.0}
    fake = _FakeLLM(
        content=[{"type": "tool_use", "id": "t", "name": "evaluate_issue", "input": bad_input}],
    )
    harness = AgentA(FallbackLLMClient(fake))
    with pytest.raises(SchemaValidationError):
        await harness.analyze(_make_input())
