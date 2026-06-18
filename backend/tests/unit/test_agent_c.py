"""Agent C schema + harness 测试（不调真实 LLM）。"""
from __future__ import annotations

from typing import Any

import pytest

from app.agents.agent_c import (
    AgentC,
    SchemaValidationError,
    ToolNotCalledError,
)
from app.agents.schemas import AgentCInput, AgentCOutput, TestResult
from app.agents.tools import SUBMIT_REVIEW_TOOL
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


VALID_APPROVED: dict[str, Any] = {
    "verdict": "APPROVED",
    "overall_score": 8.2,
    "dimensions": {
        "correctness":    {"score": 9, "passed": True,  "comment": "fix matches root cause"},
        "test_coverage":  {"score": 8, "passed": True,  "comment": "new unit test added"},
        "code_style":     {"score": 7, "passed": True,  "comment": "consistent with repo"},
        "security":       {"score": 8, "passed": True,  "comment": "no unsafe input"},
        "pr_description": {"score": 6, "passed": True,  "comment": "summary clear"},
    },
    "rejection_reason": None,
    "pr_title": "fix: correct subtract direction",
    "pr_body":  "## Summary\nFix subtract\n\nCloses #42",
    "overall_comment": "完整改动，测试齐全，可合并。",
}

VALID_REJECTED: dict[str, Any] = {
    "verdict": "REJECTED",
    "overall_score": 4.5,
    "dimensions": {
        "correctness":    {"score": 4, "passed": False, "comment": "wrong direction"},
        "test_coverage":  {"score": 6, "passed": True,  "comment": "tests present"},
        "code_style":     {"score": 7, "passed": True,  "comment": "ok"},
        "security":       {"score": 8, "passed": True,  "comment": "no concerns"},
        "pr_description": {"score": 5, "passed": True,  "comment": "ok"},
    },
    "rejection_reason": "src/calc.py:12 swap operands; current logic returns a-b but should be b-a per Issue #42.",
    "pr_title": None,
    "pr_body":  None,
    "overall_comment": "方向反了，需修正后再走评审。",
}


class TestAgentCOutputSchema:
    def test_valid_approved(self) -> None:
        out = AgentCOutput.model_validate(VALID_APPROVED)
        assert out.verdict == "APPROVED"
        assert out.pr_title and out.pr_body

    def test_valid_rejected(self) -> None:
        out = AgentCOutput.model_validate(VALID_REJECTED)
        assert out.verdict == "REJECTED"
        assert out.rejection_reason

    def test_approved_missing_pr_title_rejected(self) -> None:
        bad = {**VALID_APPROVED, "pr_title": None}
        with pytest.raises(Exception):
            AgentCOutput.model_validate(bad)

    def test_approved_missing_pr_body_rejected(self) -> None:
        bad = {**VALID_APPROVED, "pr_body": "  "}
        with pytest.raises(Exception):
            AgentCOutput.model_validate(bad)

    def test_rejected_missing_reason_rejected(self) -> None:
        bad = {**VALID_REJECTED, "rejection_reason": None}
        with pytest.raises(Exception):
            AgentCOutput.model_validate(bad)

    def test_missing_dimension_rejected(self) -> None:
        bad = {**VALID_APPROVED}
        bad["dimensions"] = {k: v for k, v in bad["dimensions"].items() if k != "security"}
        with pytest.raises(Exception):
            AgentCOutput.model_validate(bad)

    def test_dimension_score_out_of_range(self) -> None:
        bad = {**VALID_APPROVED}
        bad["dimensions"] = {**bad["dimensions"],
                             "correctness": {"score": 11, "passed": True, "comment": "x"}}
        with pytest.raises(Exception):
            AgentCOutput.model_validate(bad)


# ---------------------------------------------------------------------------
# Harness 测试
# ---------------------------------------------------------------------------


def _make_input() -> AgentCInput:
    return AgentCInput(
        issue_title="bug: subtract wrong",
        issue_body="subtract(5,3) returns 8",
        repo_full_name="o/r",
        repo_language="Python",
        diff_content="diff --git a/calc.py b/calc.py\n@@ -1 +1 @@\n-a-b\n+b-a",
        diff_summary="swap operands",
        test_result=TestResult(
            test_passed=True, total_tests=10, failed_tests=0,
            new_tests_added=1, test_output_snippet="10 passed in 0.5s",
        ),
        files_changed=["calc.py"],
        attempt_number=1,
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
            is_fallback=False, success=True, input_tokens=600,
            output_tokens=300, cost_usd=0.02, latency_ms=2500,
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
async def test_harness_approved() -> None:
    fake = _FakeLLM(
        content=[
            {"type": "text", "text": "Reviewing the diff."},
            {
                "type": "tool_use",
                "id": "toolu_y",
                "name": SUBMIT_REVIEW_TOOL.name,
                "input": VALID_APPROVED,
            },
        ],
    )
    harness = AgentC(FallbackLLMClient(fake))
    output, resp = await harness.review(_make_input())
    assert output.verdict == "APPROVED"
    assert output.pr_title and output.pr_body
    assert resp.provider == "anthropic"


@pytest.mark.asyncio
async def test_harness_rejected() -> None:
    fake = _FakeLLM(
        content=[{
            "type": "tool_use", "id": "t",
            "name": SUBMIT_REVIEW_TOOL.name,
            "input": VALID_REJECTED,
        }],
    )
    harness = AgentC(FallbackLLMClient(fake))
    output, _ = await harness.review(_make_input())
    assert output.verdict == "REJECTED"
    assert output.rejection_reason and "calc.py" in output.rejection_reason


@pytest.mark.asyncio
async def test_harness_rejects_tool_not_called() -> None:
    fake = _FakeLLM(
        content=[{"type": "text", "text": "Sorry, I won't review."}],
        stop_reason="end_turn",
    )
    harness = AgentC(FallbackLLMClient(fake))
    with pytest.raises(ToolNotCalledError):
        await harness.review(_make_input())


@pytest.mark.asyncio
async def test_harness_rejects_invalid_schema() -> None:
    bad = {**VALID_APPROVED, "overall_score": 11.0}
    fake = _FakeLLM(
        content=[{"type": "tool_use", "id": "t",
                  "name": SUBMIT_REVIEW_TOOL.name, "input": bad}],
    )
    harness = AgentC(FallbackLLMClient(fake))
    with pytest.raises(SchemaValidationError):
        await harness.review(_make_input())
