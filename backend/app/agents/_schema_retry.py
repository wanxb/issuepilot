"""Single-Shot Tool-Use harness 公共 schema 校验重试（2.3）。

工作机制：
    1. 第 1 次 call：messages=[user_msg]
    2. 输出验 schema 通过 → 返回 (parsed, resp)
    3. 失败 → 第 2 次 call：messages=[user_msg, assistant(tool_use_dummy), user(correction_hint)]
       简化版：messages=[user_msg, user(correction_hint)]（多数 provider 容忍）
    4. 仍失败 → raise SchemaValidationError

只重试一次（max_attempts=2，含初始）。LLMResponse.all_attempts 合并两次调用
的网络层尝试，方便上层 persist_attempts 一次性入库。

仅修单次 schema validation 失败，不处理 ToolNotCalledError（那是更深层的
模型不合作；继续重试也大概率徒劳）。
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from app.llm.base import LLMResponse, Message
from app.llm.fallback import FallbackLLMClient
from app.models.enums import AgentKind

log = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class SchemaValidationError(Exception):
    """LLM 输出未通过 pydantic schema 校验（两次尝试均失败）。"""

    def __init__(self, message: str, *, raw_input: dict[str, Any] | None) -> None:
        super().__init__(message)
        self.raw_input = raw_input


class ToolNotCalledError(Exception):
    """模型没调用指定终止工具（自由文本回复或调错工具）。"""


def _format_validation_errors(e: ValidationError, *, max_errors: int = 3) -> str:
    parts: list[str] = []
    for err in e.errors()[:max_errors]:
        loc = ".".join(str(x) for x in err.get("loc", ()))
        parts.append(f"  - {loc or '(root)'}: {err.get('msg', '')}")
    return "\n".join(parts)


async def call_with_schema_retry(
    *,
    llm: FallbackLLMClient,
    user_msg: str,
    system: str,
    tool: Any,                   # ToolDefinition
    output_cls: type[T],
    agent_kind: AgentKind,
    agent_label: str,            # for logs e.g. "agent_a"
    max_tokens: int,
    temperature: float,
) -> tuple[T, LLMResponse]:
    """返回 (parsed_output, llm_response)。llm_response.all_attempts 合并两轮。

    raise:
        SchemaValidationError —— 两次尝试均未通过 schema
        ToolNotCalledError    —— 模型没调用指定工具（第 1 次就立判，不重试）
    """
    messages: list[Message] = [Message(role="user", content=user_msg)]
    accumulated_attempts: list[Any] = []
    last_resp: LLMResponse | None = None
    last_validation_err: ValidationError | None = None

    for attempt_no in (1, 2):
        resp = await llm.call(
            messages=messages,
            system=system,
            tools=[tool],
            max_tokens=max_tokens,
            temperature=temperature,
            agent_kind=agent_kind,
        )
        last_resp = resp
        accumulated_attempts.extend(resp.all_attempts)

        tool_input = resp.get_tool_use(tool.name)
        if tool_input is None:
            log.warning(
                f"{agent_label}.tool_not_called",
                stop_reason=resp.stop_reason,
                attempt=attempt_no,
                text_preview=resp.text()[:200],
            )
            raise ToolNotCalledError(
                f"Model did not call {tool.name}; "
                f"stop_reason={resp.stop_reason!r}",
            )

        try:
            parsed = output_cls.model_validate(tool_input)
        except ValidationError as e:
            last_validation_err = e
            log.warning(
                f"{agent_label}.schema_validation_failed",
                attempt=attempt_no,
                errors=e.errors()[:5],
                raw=tool_input,
            )
            if attempt_no == 1:
                # 追加纠错提示后重试
                err_text = _format_validation_errors(e)
                messages = [
                    Message(role="user", content=user_msg),
                    Message(
                        role="user",
                        content=(
                            f"Your previous call to {tool.name} produced output that failed "
                            f"JSON schema validation:\n{err_text}\n\n"
                            f"Re-call {tool.name} once more with corrected fields. "
                            f"Do not output free text."
                        ),
                    ),
                ]
                continue
            # 第 2 次仍失败 → 抛出，合并的 attempts 留在 resp 上
            assert last_resp is not None
            last_resp.all_attempts = accumulated_attempts
            raise SchemaValidationError(
                f"{tool.name} output failed schema after retry: {e.errors()[:3]}",
                raw_input=tool_input,
            ) from e

        # 成功 —— 合并 attempts 后返回
        resp.all_attempts = accumulated_attempts
        return parsed, resp

    # 不可达；mypy 安抚
    assert last_validation_err is not None
    raise SchemaValidationError(
        "schema_retry_unexpected_state", raw_input=None,
    )
