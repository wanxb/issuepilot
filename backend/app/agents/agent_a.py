"""Agent A harness（IssueEvaluator）。

调用：
    harness = AgentA(llm_client)
    output, llm_response = await harness.analyze(agent_a_input)

不写 DB——返回 LLMResponse.all_attempts 让 caller 写 llm_call_logs。
schema 校验失败时抛 SchemaValidationError，caller 决定是否重试（1.2c retry
覆盖的是网络层；schema 层重试在 2.3 边界处理里）。
"""
from __future__ import annotations

from typing import Any

import structlog
from pydantic import ValidationError

from app.agents.prompts.agent_a import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_message,
)
from app.agents.schemas import AgentAInput, AgentAOutput
from app.agents.tools import EVALUATE_ISSUE_TOOL
from app.llm.base import Message
from app.llm.fallback import FallbackLLMClient
from app.models.enums import AgentKind

log = structlog.get_logger(__name__)


class SchemaValidationError(Exception):
    """LLM 输出未通过 pydantic schema 校验。"""

    def __init__(self, message: str, *, raw_input: dict[str, Any] | None) -> None:
        super().__init__(message)
        self.raw_input = raw_input


class ToolNotCalledError(Exception):
    """模型没调用 evaluate_issue 终止工具（自由文本回复或调错工具）。"""


class AgentA:
    prompt_version: str = PROMPT_VERSION

    def __init__(
        self,
        llm_client: FallbackLLMClient,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> None:
        self._llm = llm_client
        self._max_tokens = max_tokens
        self._temperature = temperature

    async def analyze(self, input: AgentAInput) -> tuple[AgentAOutput, Any]:
        """跑一次评估。返回 (parsed_output, llm_response)。"""
        user_msg = build_user_message(input)
        resp = await self._llm.call(
            messages=[Message(role="user", content=user_msg)],
            system=SYSTEM_PROMPT,
            tools=[EVALUATE_ISSUE_TOOL],
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            agent_kind=AgentKind.AGENT_A,
        )

        tool_input = resp.get_tool_use(EVALUATE_ISSUE_TOOL.name)
        if tool_input is None:
            log.warning(
                "agent_a.tool_not_called",
                stop_reason=resp.stop_reason,
                text_preview=resp.text()[:200],
            )
            raise ToolNotCalledError(
                f"Model did not call {EVALUATE_ISSUE_TOOL.name}; "
                f"stop_reason={resp.stop_reason!r}",
            )

        try:
            parsed = AgentAOutput.model_validate(tool_input)
        except ValidationError as e:
            log.warning(
                "agent_a.schema_validation_failed",
                errors=e.errors()[:5],
                raw=tool_input,
            )
            raise SchemaValidationError(
                f"evaluate_issue output failed schema: {e.errors()[:3]}",
                raw_input=tool_input,
            ) from e

        return parsed, resp
