"""Agent D schema + harness 测试（不调真实 LLM / GitHub）。"""
from __future__ import annotations

from typing import Any

import pytest

from app.agents.agent_d import (
    AgentD,
    SchemaValidationError,
    ToolNotCalledError,
)
from app.agents.schemas import AgentDInput, AgentDOutput, MergedPRSample
from app.agents.tools import REPORT_PROFILE_TOOL
from app.llm.base import (
    CallAttempt,
    LLMClient,
    LLMResponse,
    Message,
)
from app.llm.fallback import FallbackLLMClient
from app.models.enums import AgentKind


VALID: dict[str, Any] = {
    "test_command": "pytest -q",
    "install_command": "pip install -e .[dev]",
    "lint_command": "ruff check .",
    "code_style_notes": "PEP8, 4-space indent, snake_case naming, English docstrings.",
    "contributing_summary": "Branch from main, fix: prefix, sign-off required, run pre-commit.",
    "forbidden_patterns": ["do not modify generated/", "do not add runtime deps"],
    "pr_title_convention": "fix(scope): summary",
    "merged_pr_examples": [
        {
            "url": "https://github.com/o/r/pull/100",
            "title_pattern": "fix(parser): ...",
            "diff_style_note": "small focused fix, tests updated in same PR",
        }
    ],
    "profile_quality": "high",
    "quality_reason": "Clear CONTRIBUTING + 5 consistent merged PRs.",
}


class TestAgentDOutputSchema:
    def test_valid(self) -> None:
        out = AgentDOutput.model_validate(VALID)
        assert out.profile_quality == "high"
        assert len(out.merged_pr_examples) == 1

    def test_low_quality_still_requires_commands(self) -> None:
        ok = {**VALID, "profile_quality": "low", "quality_reason": "sparse repo"}
        # 即使 low 也必须有 test_command/install_command（schema 仍然 require）
        AgentDOutput.model_validate(ok)

    def test_invalid_quality_rejected(self) -> None:
        bad = {**VALID, "profile_quality": "excellent"}
        with pytest.raises(Exception):
            AgentDOutput.model_validate(bad)

    def test_too_many_pr_examples_rejected(self) -> None:
        bad = {**VALID, "merged_pr_examples": [VALID["merged_pr_examples"][0]] * 4}
        with pytest.raises(Exception):
            AgentDOutput.model_validate(bad)

    def test_quality_reason_max_length(self) -> None:
        bad = {**VALID, "quality_reason": "x" * 250}
        with pytest.raises(Exception):
            AgentDOutput.model_validate(bad)


# ---------------------------------------------------------------------------


def _make_input() -> AgentDInput:
    return AgentDInput(
        repo_full_name="owner/repo",
        repo_language="python",
        readme_content="# Repo\nA library for ...",
        contributing_content="Branch from main; use fix: prefix.",
        package_manifest_path="pyproject.toml",
        package_manifest_content="[project]\nname=\"x\"",
        test_workflow_content="name: test\non: [push]\njobs:\n  test:\n    steps:\n      - run: pytest",
        merged_pr_samples=[
            MergedPRSample(
                url="https://github.com/o/r/pull/100",
                title="fix(parser): off-by-one",
                diff_snippet="diff --git a/parser.py b/parser.py\n@@ -1 +1 @@\n-a\n+b\n",
            ),
        ],
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
            is_fallback=False, success=True, input_tokens=800,
            output_tokens=400, cost_usd=0.03, latency_ms=2200,
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
            {
                "type": "tool_use",
                "id": "toolu_z",
                "name": REPORT_PROFILE_TOOL.name,
                "input": VALID,
            },
        ],
    )
    harness = AgentD(FallbackLLMClient(fake))
    output, resp = await harness.generate_profile(_make_input())
    assert output.profile_quality == "high"
    assert output.test_command == "pytest -q"
    assert resp.provider == "anthropic"


@pytest.mark.asyncio
async def test_harness_rejects_tool_not_called() -> None:
    fake = _FakeLLM(
        content=[{"type": "text", "text": "I can't profile."}],
        stop_reason="end_turn",
    )
    harness = AgentD(FallbackLLMClient(fake))
    with pytest.raises(ToolNotCalledError):
        await harness.generate_profile(_make_input())


@pytest.mark.asyncio
async def test_harness_rejects_invalid_schema() -> None:
    bad = {**VALID, "profile_quality": "stellar"}
    fake = _FakeLLM(
        content=[{
            "type": "tool_use", "id": "t",
            "name": REPORT_PROFILE_TOOL.name, "input": bad,
        }],
    )
    harness = AgentD(FallbackLLMClient(fake))
    with pytest.raises(SchemaValidationError):
        await harness.generate_profile(_make_input())
