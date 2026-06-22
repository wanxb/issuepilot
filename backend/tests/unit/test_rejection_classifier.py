"""RejectionClassifier schema + harness 测试（2.3）。

不调真实 LLM；模仿 test_agent_c.py 范式。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.agents.rejection_classifier import (
    RejectionClassifier,
    SchemaValidationError,
    ToolNotCalledError,
)
from app.agents.schemas import (
    RejectionClassifierInput,
    RejectionClassifierOutput,
)
from app.agents.tools import CLASSIFY_REJECTION_TOOL
from app.llm.base import CallAttempt, LLMClient, LLMResponse, Message
from app.llm.fallback import FallbackLLMClient
from app.models.enums import AgentKind


VALID_OUTPUT: dict[str, Any] = {
    "category": "style_mismatch",
    "severity": "minor",
    "dimension": "code_style",
    "agent_b_attribution": "yes",
    "classified_reason": "maintainer asked to use snake_case per CONTRIBUTING.md",
}


class TestSchema:
    def test_valid(self) -> None:
        out = RejectionClassifierOutput.model_validate(VALID_OUTPUT)
        assert out.category == "style_mismatch"
        assert out.severity == "minor"
        assert out.dimension == "code_style"
        assert out.agent_b_attribution == "yes"

    def test_dimension_null_ok(self) -> None:
        out = RejectionClassifierOutput.model_validate(
            {**VALID_OUTPUT, "dimension": None, "category": "duplicate",
             "agent_b_attribution": "no"},
        )
        assert out.dimension is None

    def test_invalid_category_rejected(self) -> None:
        with pytest.raises(Exception):
            RejectionClassifierOutput.model_validate(
                {**VALID_OUTPUT, "category": "garbage"},
            )

    def test_invalid_severity_rejected(self) -> None:
        with pytest.raises(Exception):
            RejectionClassifierOutput.model_validate(
                {**VALID_OUTPUT, "severity": "huge"},
            )

    def test_empty_reason_rejected(self) -> None:
        with pytest.raises(Exception):
            RejectionClassifierOutput.model_validate(
                {**VALID_OUTPUT, "classified_reason": "   "},
            )

    def test_reason_too_long_rejected(self) -> None:
        with pytest.raises(Exception):
            RejectionClassifierOutput.model_validate(
                {**VALID_OUTPUT, "classified_reason": "x" * 1000},
            )


class _FakeLLM(LLMClient):
    def __init__(self, content: list[dict[str, Any]], stop_reason: str = "tool_use") -> None:
        self.provider = "anthropic"
        self.model = "claude-haiku-4-5-20251001"
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
            is_fallback=False, success=True, input_tokens=300,
            output_tokens=80, cost_usd=0.001, latency_ms=900,
        )
        return LLMResponse(
            content=self._content, stop_reason=self._stop,
            model=self.model, provider=self.provider,
            is_fallback=False, final_attempt=attempt, all_attempts=[attempt],
        )


def _input() -> RejectionClassifierInput:
    return RejectionClassifierInput(
        raw_text="please rename to snake_case to match our style",
        source="maintainer_review",
        pr_title="fix: subtract direction",
        diff_summary="swap operands",
        files_changed=["calc.py"],
        agent_c_verdict="APPROVED",
    )


@pytest.mark.asyncio
class TestHarness:
    async def test_happy_path(self) -> None:
        fake = _FakeLLM(content=[
            {"type": "text", "text": "Classifying..."},
            {"type": "tool_use", "id": "t1",
             "name": CLASSIFY_REJECTION_TOOL.name,
             "input": VALID_OUTPUT},
        ])
        harness = RejectionClassifier(FallbackLLMClient(fake))
        out, resp = await harness.classify(_input())
        assert out.category == "style_mismatch"
        assert resp.model == "claude-haiku-4-5-20251001"

    async def test_tool_not_called(self) -> None:
        fake = _FakeLLM(content=[{"type": "text", "text": "I refuse"}],
                        stop_reason="end_turn")
        harness = RejectionClassifier(FallbackLLMClient(fake))
        with pytest.raises(ToolNotCalledError):
            await harness.classify(_input())

    async def test_invalid_schema(self) -> None:
        fake = _FakeLLM(content=[{
            "type": "tool_use", "id": "t",
            "name": CLASSIFY_REJECTION_TOOL.name,
            "input": {**VALID_OUTPUT, "severity": "catastrophic"},
        }])
        harness = RejectionClassifier(FallbackLLMClient(fake))
        with pytest.raises(SchemaValidationError):
            await harness.classify(_input())
