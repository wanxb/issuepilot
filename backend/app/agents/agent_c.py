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

from app.agents._schema_retry import (
    SchemaValidationError,  # re-export
    ToolNotCalledError,     # re-export
    call_with_schema_retry,
)
from app.agents.prompts.agent_c import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_message,
)
from app.agents.schemas import AgentCInput, AgentCOutput
from app.agents.tools import SUBMIT_REVIEW_TOOL
from app.llm.fallback import FallbackLLMClient
from app.models.enums import AgentKind

log = structlog.get_logger(__name__)

__all__ = ["AgentC", "SchemaValidationError", "ToolNotCalledError"]


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
        """跑一次评审。返回 (parsed_output, llm_response)。

        2.3：schema 校验失败时内部 retry 一次。
        """
        return await call_with_schema_retry(
            llm=self._llm,
            user_msg=build_user_message(input),
            system=SYSTEM_PROMPT,
            tool=SUBMIT_REVIEW_TOOL,
            output_cls=AgentCOutput,
            agent_kind=AgentKind.AGENT_C,
            agent_label="agent_c",
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
