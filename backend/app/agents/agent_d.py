"""Agent D harness（RepoOnboarding profiler）。

Single-Shot Tool Use（与 Agent A / C 同构）。
调用：
    harness = AgentD(llm_client)
    output, llm_response = await harness.generate_profile(agent_d_input)

不写 DB —— profile_worker 拿 LLMResponse.all_attempts 写 llm_call_logs。
schema 校验失败抛 SchemaValidationError，模型未调 tool 抛 ToolNotCalledError，
caller 决定是否重试。
"""
from __future__ import annotations

from typing import Any

import structlog
from pydantic import ValidationError

from app.agents.prompts.agent_d import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_message,
)
from app.agents.schemas import AgentDInput, AgentDOutput
from app.agents.tools import REPORT_PROFILE_TOOL
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
    """模型没调用 report_profile 终止工具。"""


class AgentD:
    prompt_version: str = PROMPT_VERSION

    def __init__(
        self,
        llm_client: FallbackLLMClient,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> None:
        self._llm = llm_client
        self._max_tokens = max_tokens
        self._temperature = temperature

    async def generate_profile(self, input: AgentDInput) -> tuple[AgentDOutput, Any]:
        """跑一次 profile 生成。返回 (parsed_output, llm_response)。"""
        user_msg = build_user_message(input)
        resp = await self._llm.call(
            messages=[Message(role="user", content=user_msg)],
            system=SYSTEM_PROMPT,
            tools=[REPORT_PROFILE_TOOL],
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            agent_kind=AgentKind.AGENT_D,
        )

        tool_input = resp.get_tool_use(REPORT_PROFILE_TOOL.name)
        if tool_input is None:
            log.warning(
                "agent_d.tool_not_called",
                stop_reason=resp.stop_reason,
                text_preview=resp.text()[:200],
            )
            raise ToolNotCalledError(
                f"Model did not call {REPORT_PROFILE_TOOL.name}; "
                f"stop_reason={resp.stop_reason!r}",
            )

        try:
            parsed = AgentDOutput.model_validate(tool_input)
        except ValidationError as e:
            log.warning(
                "agent_d.schema_validation_failed",
                errors=e.errors()[:5],
                raw=tool_input,
            )
            raise SchemaValidationError(
                f"report_profile output failed schema: {e.errors()[:3]}",
                raw_input=tool_input,
            ) from e

        return parsed, resp
