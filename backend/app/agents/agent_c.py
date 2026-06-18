"""Agent C harness（CodeReviewer）。

Single-Shot Tool Use（与 Agent A 同构）。
调用：
    harness = AgentC(llm_client)
    output, llm_response = await harness.review(agent_c_input)

不写 DB —— review_worker 拿 LLMResponse.all_attempts 写 llm_call_logs。
schema 校验失败抛 SchemaValidationError，model 未调 tool 抛 ToolNotCalledError，
caller 决定是否重试（2.3 边界处理覆盖 schema 层重试）。
"""
from __future__ import annotations

from typing import Any

import structlog
from pydantic import ValidationError

from app.agents.prompts.agent_c import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_message,
)
from app.agents.schemas import AgentCInput, AgentCOutput
from app.agents.tools import SUBMIT_REVIEW_TOOL
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
    """模型没调用 submit_review 终止工具。"""


class AgentC:
    prompt_version: str = PROMPT_VERSION

    def __init__(
        self,
        llm_client: FallbackLLMClient,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> None:
        self._llm = llm_client
        self._max_tokens = max_tokens
        self._temperature = temperature

    async def review(self, input: AgentCInput) -> tuple[AgentCOutput, Any]:
        """跑一次评审。返回 (parsed_output, llm_response)。"""
        user_msg = build_user_message(input)
        resp = await self._llm.call(
            messages=[Message(role="user", content=user_msg)],
            system=SYSTEM_PROMPT,
            tools=[SUBMIT_REVIEW_TOOL],
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            agent_kind=AgentKind.AGENT_C,
        )

        tool_input = resp.get_tool_use(SUBMIT_REVIEW_TOOL.name)
        if tool_input is None:
            log.warning(
                "agent_c.tool_not_called",
                stop_reason=resp.stop_reason,
                text_preview=resp.text()[:200],
            )
            raise ToolNotCalledError(
                f"Model did not call {SUBMIT_REVIEW_TOOL.name}; "
                f"stop_reason={resp.stop_reason!r}",
            )

        try:
            parsed = AgentCOutput.model_validate(tool_input)
        except ValidationError as e:
            log.warning(
                "agent_c.schema_validation_failed",
                errors=e.errors()[:5],
                raw=tool_input,
            )
            raise SchemaValidationError(
                f"submit_review output failed schema: {e.errors()[:3]}",
                raw_input=tool_input,
            ) from e

        return parsed, resp
