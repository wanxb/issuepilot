"""RejectionClassifier harness（2.3）。

调用：
    harness = RejectionClassifier(llm_client)
    output, llm_response = await harness.classify(input)

不写 DB；caller 负责 UPDATE rejection_reasons 与 llm_call_logs。
"""
from __future__ import annotations

from typing import Any

import structlog
from pydantic import ValidationError

from app.agents.prompts.rejection_classifier import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_message,
)
from app.agents.schemas import (
    RejectionClassifierInput,
    RejectionClassifierOutput,
)
from app.agents.tools import CLASSIFY_REJECTION_TOOL
from app.llm.base import Message
from app.llm.fallback import FallbackLLMClient
from app.models.enums import AgentKind

log = structlog.get_logger(__name__)


class SchemaValidationError(Exception):
    def __init__(self, message: str, *, raw_input: dict[str, Any] | None) -> None:
        super().__init__(message)
        self.raw_input = raw_input


class ToolNotCalledError(Exception):
    """模型没调用 classify_rejection 工具。"""


class RejectionClassifier:
    prompt_version: str = PROMPT_VERSION

    def __init__(
        self,
        llm_client: FallbackLLMClient,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> None:
        self._llm = llm_client
        self._max_tokens = max_tokens
        self._temperature = temperature

    async def classify(
        self, input: RejectionClassifierInput,
    ) -> tuple[RejectionClassifierOutput, Any]:
        user_msg = build_user_message(input)
        resp = await self._llm.call(
            messages=[Message(role="user", content=user_msg)],
            system=SYSTEM_PROMPT,
            tools=[CLASSIFY_REJECTION_TOOL],
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            agent_kind=AgentKind.REJECTION_CLASSIFIER,
        )

        tool_input = resp.get_tool_use(CLASSIFY_REJECTION_TOOL.name)
        if tool_input is None:
            log.warning(
                "rejection_classifier.tool_not_called",
                stop_reason=resp.stop_reason,
                text_preview=resp.text()[:200],
            )
            raise ToolNotCalledError(
                f"Model did not call {CLASSIFY_REJECTION_TOOL.name}; "
                f"stop_reason={resp.stop_reason!r}",
            )

        try:
            parsed = RejectionClassifierOutput.model_validate(tool_input)
        except ValidationError as e:
            log.warning(
                "rejection_classifier.schema_validation_failed",
                errors=e.errors()[:5], raw=tool_input,
            )
            raise SchemaValidationError(
                f"classify_rejection output failed schema: {e.errors()[:3]}",
                raw_input=tool_input,
            ) from e

        return parsed, resp
