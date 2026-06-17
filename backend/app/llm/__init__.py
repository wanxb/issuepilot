"""LLM 抽象层。

设计要点（见 docs/ARCHITECTURE.md §7）：
    - LLMClient ABC：定义 call() 契约
    - AnthropicClient：基于 anthropic SDK，支持中转 (ANTHROPIC_BASE_URL + AUTH_TOKEN)
    - FallbackLLMClient：单调用级混合粒度兜底（retry primary → switch fallback）
    - 调用方负责把 LLMResponse.all_attempts 写入 llm_call_logs 表

使用：
    from app.llm.factory import build_client
    client = build_client("agent_a")
    resp = await client.call(messages=..., tools=..., agent_kind=AgentKind.AGENT_A)
"""
from app.llm.base import (
    CallAttempt,
    LLMCallError,
    LLMClient,
    LLMResponse,
    RetryConfig,
    ToolDefinition,
)

__all__ = [
    "CallAttempt",
    "LLMCallError",
    "LLMClient",
    "LLMResponse",
    "RetryConfig",
    "ToolDefinition",
]
