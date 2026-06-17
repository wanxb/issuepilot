"""LLM 抽象基类与共用类型。

设计原则：
    - LLMClient 不碰 DB；通过 LLMResponse.all_attempts 把每次重试都告诉调用方
    - 错误分两类：RetriableError（网络/限流/5xx）和 PermanentError（认证/输入/Schema）
    - all_attempts 是事实，写不写 DB 由 caller 决定
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from app.models.enums import AgentKind

# ---------------------------------------------------------------------------
# 消息 / 工具类型（贴合 Anthropic Messages API 形态）
# ---------------------------------------------------------------------------

Role = Literal["user", "assistant", "system"]


@dataclass(slots=True)
class Message:
    role: Role
    content: str | list[dict[str, Any]]


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]

    def to_anthropic(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


# ---------------------------------------------------------------------------
# 重试配置
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RetryConfig:
    max_attempts: int = 3
    backoff_seconds: tuple[float, ...] = (2.0, 5.0, 10.0)
    retry_on_http: tuple[int, ...] = (408, 429, 500, 502, 503, 504)

    def sleep_for(self, attempt_number: int) -> float:
        """attempt_number 从 1 开始；返回此次失败后要 sleep 的秒数。"""
        idx = min(attempt_number - 1, len(self.backoff_seconds) - 1)
        return self.backoff_seconds[idx] if self.backoff_seconds else 0.0


# ---------------------------------------------------------------------------
# 调用记录
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CallAttempt:
    """一次 LLM HTTP 请求的元数据（成功或失败）。"""

    attempt_number: int
    provider: str
    model: str
    is_fallback: bool
    success: bool
    error_code: str | None = None
    error_detail: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


@dataclass(slots=True)
class LLMResponse:
    """LLMClient.call() 的返回值。成功调用必有 final_attempt.success=True。"""

    content: list[dict[str, Any]]   # ContentBlock 列表（text / tool_use 混合）
    stop_reason: str
    model: str
    provider: str
    is_fallback: bool
    final_attempt: CallAttempt
    all_attempts: list[CallAttempt] = field(default_factory=list)

    def get_tool_use(self, name: str) -> dict[str, Any] | None:
        """便捷方法：取指定 tool 的 input 字典；没有则返回 None。"""
        for block in self.content:
            if block.get("type") == "tool_use" and block.get("name") == name:
                return block.get("input")
        return None

    def text(self) -> str:
        return "".join(
            block.get("text", "") for block in self.content if block.get("type") == "text"
        )


# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------


class LLMCallError(Exception):
    """LLM 调用最终失败（已耗尽所有重试 + fallback）。"""

    def __init__(
        self,
        message: str,
        *,
        attempts: list[CallAttempt],
    ) -> None:
        super().__init__(message)
        self.attempts = attempts


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------


class LLMClient(ABC):
    """单一 provider 的 LLM 客户端契约。"""

    provider: str
    model: str
    is_fallback: bool = False

    @abstractmethod
    async def call(
        self,
        *,
        messages: list[Message],
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
        tool_choice: dict[str, Any] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        agent_kind: AgentKind = AgentKind.OTHER,
        attempt_number: int = 1,
    ) -> LLMResponse:
        """单次 HTTP 请求；不做重试，不写 DB。"""

    @abstractmethod
    async def aclose(self) -> None: ...
