"""Agent A harness（IssueEvaluator）。

调用：
    harness = AgentA(llm_client)
    output, llm_response = await harness.analyze(agent_a_input)

不写 DB——返回 LLMResponse.all_attempts 让 caller 写 llm_call_logs。
schema 校验失败时抛 SchemaValidationError，caller 决定是否重试（1.2c retry
覆盖的是网络层；schema 层重试在 2.3 边界处理里）。
"""
from __future__ import annotations

from typing import Any  # noqa: F401  保留方便上游导入

import structlog

from app.agents._schema_retry import (
    SchemaValidationError,  # re-export 保持向后兼容
    ToolNotCalledError,     # re-export
    call_with_schema_retry,
)
from app.agents.prompts.agent_a import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_message,
)
from app.agents.schemas import AgentAInput, AgentAOutput
from app.agents.tools import EVALUATE_ISSUE_TOOL
from app.llm.fallback import FallbackLLMClient
from app.models.enums import AgentKind

log = structlog.get_logger(__name__)

__all__ = ["AgentA", "SchemaValidationError", "ToolNotCalledError"]


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
        """跑一次评估。返回 (parsed_output, llm_response)。

        2.3：schema 校验失败时内部 retry 一次（call_with_schema_retry）。
        """
        return await call_with_schema_retry(
            llm=self._llm,
            user_msg=build_user_message(input),
            system=SYSTEM_PROMPT,
            tool=EVALUATE_ISSUE_TOOL,
            output_cls=AgentAOutput,
            agent_kind=AgentKind.AGENT_A,
            agent_label="agent_a",
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
