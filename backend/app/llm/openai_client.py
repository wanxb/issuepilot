"""OpenAI Chat Completions client（2.5）。

直接走 httpx，不依赖 openai SDK（避免引入额外大包）。Chat Completions API
+ tools 是 OpenAI 与 DeepSeek、智谱、阿里、Moonshot 等"OpenAI 兼容"
provider 的最大公因子。

设计：
    - 实现 app.llm.base.LLMClient 抽象
    - 翻译层：
        * 入参 tools = list[ToolDefinition (Anthropic schema)] → OpenAI tools
        * 响应 choices[0].message.tool_calls → 我们的 content 块（type=tool_use）
    - 错误分类：
        * 408/429/5xx → _RetriableError（让 FallbackLLMClient retry / switch）
        * 401/400/422 → _PermanentError（输入/认证问题，不重试）
    - 成本估算：复用 anthropic_client._estimate_cost（同价目表，按 model 子串）
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.llm.anthropic_client import _RetriableError, _PermanentError, _estimate_cost
from app.llm.base import (
    CallAttempt,
    LLMClient,
    LLMResponse,
    Message,
    ToolDefinition,
)
from app.models.enums import AgentKind

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIClient(LLMClient):
    def __init__(
        self,
        model: str,
        *,
        provider_label: str = "openai",
        api_key: str | None = None,
        base_url: str | None = None,
        is_fallback: bool = False,
        timeout: float = 120.0,
    ) -> None:
        if not api_key:
            raise ValueError(
                f"{provider_label} requires api_key (set in models.yaml api_key_env)",
            )
        self.model = model
        self.provider = provider_label
        self.is_fallback = is_fallback
        self._client = httpx.AsyncClient(
            base_url=base_url or DEFAULT_BASE_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "IssuePilot/0.1",
            },
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

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
        start = time.perf_counter()

        # 拼装 OpenAI Chat Completions 格式 messages
        chat_messages: list[dict[str, Any]] = []
        if system:
            chat_messages.append({"role": "system", "content": system})
        for m in messages:
            chat_messages.append(_message_to_openai(m))

        body: dict[str, Any] = {
            "model": self.model,
            "messages": chat_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = [_tool_to_openai(t) for t in tools]
            # 默认让模型自己决定调哪个工具；harness 层用 tool_choice 强制时支持
            if tool_choice and tool_choice.get("type") == "tool":
                body["tool_choice"] = {
                    "type": "function",
                    "function": {"name": tool_choice.get("name", "")},
                }
            else:
                body["tool_choice"] = "auto"

        try:
            resp = await self._client.post("/chat/completions", json=body)
        except httpx.TimeoutException as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            raise _RetriableError(_attempt(
                self, attempt_number, success=False,
                error_code="timeout", detail=str(e)[:1000],
                latency_ms=elapsed_ms,
            )) from e
        except httpx.ConnectError as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            raise _RetriableError(_attempt(
                self, attempt_number, success=False,
                error_code="connection", detail=str(e)[:1000],
                latency_ms=elapsed_ms,
            )) from e

        elapsed_ms = int((time.perf_counter() - start) * 1000)

        if resp.status_code != 200:
            err_code = f"http_{resp.status_code}"
            err_detail = resp.text[:1000]
            attempt = _attempt(
                self, attempt_number, success=False,
                error_code=err_code, detail=err_detail, latency_ms=elapsed_ms,
            )
            # 408 / 429 / 5xx → retriable；其余 → permanent
            if resp.status_code in (408, 429) or resp.status_code >= 500:
                raise _RetriableError(attempt)
            raise _PermanentError(attempt)

        data = resp.json()
        usage = data.get("usage") or {}
        in_tok = int(usage.get("prompt_tokens", 0))
        out_tok = int(usage.get("completion_tokens", 0))
        cost = _estimate_cost(self.model, in_tok, out_tok)

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        stop_reason = _map_finish_reason(choice.get("finish_reason", ""))

        content_blocks = _translate_response(message)

        attempt = CallAttempt(
            attempt_number=attempt_number,
            provider=self.provider,
            model=self.model,
            is_fallback=self.is_fallback,
            success=True,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            latency_ms=elapsed_ms,
        )
        return LLMResponse(
            content=content_blocks,
            stop_reason=stop_reason,
            model=self.model,
            provider=self.provider,
            is_fallback=self.is_fallback,
            final_attempt=attempt,
            all_attempts=[attempt],
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _attempt(
    client: OpenAIClient, attempt_number: int, *,
    success: bool, error_code: str | None = None, detail: str | None = None,
    latency_ms: int = 0,
) -> CallAttempt:
    return CallAttempt(
        attempt_number=attempt_number,
        provider=client.provider, model=client.model,
        is_fallback=client.is_fallback,
        success=success, error_code=error_code, error_detail=detail,
        latency_ms=latency_ms,
    )


def _message_to_openai(m: Message) -> dict[str, Any]:
    """简化版：当前 harness 只用 user/assistant text；不处理 tool_result 回执。"""
    if isinstance(m.content, str):
        return {"role": m.role, "content": m.content}
    # list[dict] 情况：取所有 text 块拼接（OpenAI Chat Completions 不直接支持
    # Anthropic 的混合 content 块；harness 层目前也不传 list-form content）
    text = " ".join(
        b.get("text", "") for b in m.content
        if isinstance(b, dict) and b.get("type") == "text"
    )
    return {"role": m.role, "content": text}


def _tool_to_openai(t: ToolDefinition) -> dict[str, Any]:
    """ToolDefinition (Anthropic schema) → OpenAI function tool。

    Anthropic：{name, description, input_schema}
    OpenAI ：  {type:"function", function: {name, description, parameters}}
    """
    return {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": t.input_schema,
        },
    }


def _translate_response(message: dict[str, Any]) -> list[dict[str, Any]]:
    """OpenAI message → Anthropic-style content blocks。

    OpenAI 的 assistant message 形如：
        {"role":"assistant","content":"some text or null","tool_calls":[
            {"id":"call_xxx","type":"function",
             "function":{"name":"foo","arguments":"<JSON string>"}}, ...]}

    我们把 content (str) 转为 type=text 块；tool_calls 每条转 type=tool_use 块，
    并把 function.arguments 反序列化为 dict（其值就是 Anthropic 的 input 字段）。
    """
    blocks: list[dict[str, Any]] = []
    text = message.get("content")
    if isinstance(text, str) and text.strip():
        blocks.append({"type": "text", "text": text})

    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict) or call.get("type") != "function":
            continue
        fn = call.get("function") or {}
        name = fn.get("name") or ""
        raw_args = fn.get("arguments")
        # OpenAI 返回 arguments 是 JSON 字符串；少数 provider（如 DeepSeek
        # 部分版本）会直接返回 object，做兼容
        if isinstance(raw_args, str):
            try:
                input_obj = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                input_obj = {"_raw_arguments": raw_args}
        elif isinstance(raw_args, dict):
            input_obj = raw_args
        else:
            input_obj = {}
        blocks.append({
            "type": "tool_use",
            "id": call.get("id", "call_unknown"),
            "name": name,
            "input": input_obj,
        })
    return blocks


def _map_finish_reason(finish_reason: str) -> str:
    """OpenAI finish_reason → 我们用的 stop_reason（与 Anthropic 对齐）。"""
    mapping = {
        "stop": "end_turn",
        "tool_calls": "tool_use",
        "function_call": "tool_use",   # legacy
        "length": "max_tokens",
        "content_filter": "stop_sequence",
    }
    return mapping.get(finish_reason, finish_reason or "end_turn")
