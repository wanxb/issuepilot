"""RejectionClassifier harness（2.3）。

调用：
    harness = RejectionClassifier(llm_client)
    output, llm_response = await harness.classify(input)

不写 DB；caller 负责 UPDATE rejection_reasons 与 llm_call_logs。
"""
from __future__ import annotations

from typing import Any

import structlog

from app.agents._schema_retry import (
    SchemaValidationError,  # re-export
    ToolNotCalledError,     # re-export
    call_with_schema_retry,
)
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
from app.llm.fallback import FallbackLLMClient
from app.models.enums import AgentKind

log = structlog.get_logger(__name__)

__all__ = [
    "RejectionClassifier", "SchemaValidationError", "ToolNotCalledError",
]


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
        """2.3：schema 校验失败时内部 retry 一次。"""
        return await call_with_schema_retry(
            llm=self._llm,
            user_msg=build_user_message(input),
            system=SYSTEM_PROMPT,
            tool=CLASSIFY_REJECTION_TOOL,
            output_cls=RejectionClassifierOutput,
            agent_kind=AgentKind.REJECTION_CLASSIFIER,
            agent_label="rejection_classifier",
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
