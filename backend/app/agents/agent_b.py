"""Agent B harness —— stream-json 解析器。

与 Agent A 不同，Agent B 不直接调用 LLM。它解析 Claude Code CLI 的
--output-format stream-json 输出流，从中提取：
    1. 日志条目（step / level / message），用于 DevLog 写入 + WebSocket 推送
    2. report_completion / report_failure 工具调用的参数
    3. 最终 result 事件的 cost/turns 元数据

每次 parse_stream_line() 调用处理一行原始文本，返回 ParsedLine dataclass。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog

from app.agents.prompts.agent_b import PROMPT_VERSION, SYSTEM_PROMPT, build_prompt
from app.agents.schemas import AgentBFailure, AgentBInput, AgentBOutput
from app.models.enums import DevLogLevel, DevLogStep

log = structlog.get_logger(__name__)

# MCP tool 名称前缀（Claude Code CLI 拼接 mcpServer name + tool name）
_MCP_SERVER = "issuepilot-agent-b-terminator"
_TOOL_COMPLETION = f"mcp__{_MCP_SERVER}__report_completion"
_TOOL_FAILURE = f"mcp__{_MCP_SERVER}__report_failure"


@dataclass
class ParsedLine:
    step: DevLogStep
    level: DevLogLevel
    message: str
    # 结构化报告（只在解析到 report_completion / report_failure 时非 None）
    report_kind: Literal["completion", "failure"] | None = None
    report_payload: dict[str, Any] | None = None
    # result 事件元数据
    num_turns: int | None = None
    total_cost_usd: float | None = None
    stop_reason: str | None = None
    # 2.3 卡死检测：本行 assistant 调用的工具简名（Read/Edit/Bash/...）
    # 一行可能有多个 tool_use；取最后一个用于状态推进
    tool_name: str | None = None


def _detect_step(text: str) -> DevLogStep:
    """从文本内容推断 DevLogStep（按关键词匹配，不精确但够用）。"""
    lower = text.lower()
    if any(k in lower for k in ("=== setup", "cloning", "git clone", "branch")):
        return DevLogStep.SETUP
    if any(k in lower for k in ("=== system", "agent b finished", "agent b starting")):
        return DevLogStep.SYSTEM
    if "commit" in lower:
        return DevLogStep.COMMIT
    if any(k in lower for k in ("pytest", "test", "passed", "failed", "error")):
        return DevLogStep.TEST
    if any(k in lower for k in ("implement", "edit", "write", "fix", "change")):
        return DevLogStep.IMPLEMENT
    if any(k in lower for k in ("plan", "strategy", "approach")):
        return DevLogStep.PLAN
    if any(k in lower for k in ("analyze", "read", "understand", "look", "search")):
        return DevLogStep.ANALYZE
    return DevLogStep.IMPLEMENT


class AgentB:
    prompt_version: str = PROMPT_VERSION
    system_prompt: str = SYSTEM_PROMPT

    def build_prompt(self, input: AgentBInput) -> str:
        return build_prompt(input)

    def parse_stream_line(self, raw_line: str) -> ParsedLine:
        """解析 Claude Code stream-json 的一行输出。

        非 JSON 行（如 entrypoint.sh 的 echo 输出）直接透传为 INFO 日志。
        JSON 行按 type 字段路由：
            - "assistant": 提取 text 内容 + 检测 tool_use（report_completion/failure）
            - "result": 提取 cost/turns/stop_reason
            - 其他: DEBUG 透传
        """
        stripped = raw_line.strip()
        if not stripped:
            return ParsedLine(step=DevLogStep.SYSTEM, level=DevLogLevel.DEBUG, message="")

        # 非 JSON 行（entrypoint.sh 的 echo）
        if not stripped.startswith("{"):
            step = _detect_step(stripped)
            level = DevLogLevel.ERROR if "error" in stripped.lower() else DevLogLevel.INFO
            return ParsedLine(step=step, level=level, message=stripped)

        # JSON 行
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            return ParsedLine(step=DevLogStep.SYSTEM, level=DevLogLevel.DEBUG, message=stripped[:500])

        event_type = obj.get("type", "")

        # --- assistant 消息 ---
        if event_type == "assistant":
            return self._parse_assistant_event(obj)

        # --- result 事件（最终摘要）---
        if event_type == "result":
            return self._parse_result_event(obj)

        # --- user 消息（tool result 回显，通常是 "OK: completion report saved"）---
        if event_type == "user":
            content_list = obj.get("message", {}).get("content", [])
            texts = [
                b.get("content", "")
                for b in content_list
                if isinstance(b, dict) and b.get("type") == "tool_result"
            ]
            msg = " | ".join(str(t) for t in texts if t)[:300]
            return ParsedLine(step=DevLogStep.SYSTEM, level=DevLogLevel.DEBUG, message=msg or stripped[:200])

        # --- system / 其他 ---
        return ParsedLine(step=DevLogStep.SYSTEM, level=DevLogLevel.DEBUG, message=stripped[:300])

    def _parse_assistant_event(self, obj: dict[str, Any]) -> ParsedLine:
        content_list = obj.get("message", {}).get("content", [])
        texts: list[str] = []
        report_kind = None
        report_payload = None
        last_tool_name: str | None = None

        for block in content_list:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                texts.append(block.get("text", ""))
            elif btype == "tool_use":
                tool_name = block.get("name", "")
                tool_input = block.get("input", {})
                if tool_name == _TOOL_COMPLETION:
                    report_kind = "completion"
                    report_payload = tool_input
                    texts.append(f"[report_completion called]")
                elif tool_name == _TOOL_FAILURE:
                    report_kind = "failure"
                    report_payload = tool_input
                    texts.append(f"[report_failure called: {tool_input.get('reason', '?')}]")
                else:
                    texts.append(f"[tool: {tool_name}]")
                # 2.3 卡死检测：记录非 MCP 的工具名，按短名（"Read" / "Bash" / ...）
                short = tool_name.rsplit("__", 1)[-1] if "__" in tool_name else tool_name
                if short and short not in {"report_completion", "report_failure"}:
                    last_tool_name = short

        message = " ".join(t.strip() for t in texts if t.strip())[:500]
        if not message:
            message = "[assistant turn]"

        step = _detect_step(message)
        level = DevLogLevel.INFO

        if report_kind == "failure":
            level = DevLogLevel.WARNING
        elif "[tool:" in message.lower():
            level = DevLogLevel.DEBUG

        result = ParsedLine(step=step, level=level, message=message)
        result.report_kind = report_kind
        result.report_payload = report_payload
        result.tool_name = last_tool_name
        return result

    def _parse_result_event(self, obj: dict[str, Any]) -> ParsedLine:
        subtype = obj.get("subtype", "")
        turns = obj.get("num_turns")
        cost = obj.get("total_cost_usd")
        is_error = obj.get("is_error", False)

        msg_parts: list[str] = [f"[Claude Code finished: {subtype}"]
        if turns is not None:
            msg_parts.append(f"turns={turns}")
        if cost is not None:
            msg_parts.append(f"cost=${cost:.4f}")
        msg_parts.append("]")
        message = " ".join(msg_parts)

        level = DevLogLevel.ERROR if is_error else DevLogLevel.INFO
        result = ParsedLine(step=DevLogStep.SYSTEM, level=level, message=message)
        result.num_turns = turns
        result.total_cost_usd = cost
        result.stop_reason = subtype
        return result

    def extract_output(
        self,
        report_kind: str | None,
        report_payload: dict[str, Any] | None,
    ) -> AgentBOutput | AgentBFailure | None:
        """从 report_completion / report_failure payload 构造结构化输出。"""
        if report_kind == "completion" and report_payload:
            try:
                return AgentBOutput.model_validate(report_payload)
            except Exception as e:
                log.warning("agent_b.output_parse_failed", error=str(e), payload=report_payload)
                return None
        if report_kind == "failure" and report_payload:
            try:
                return AgentBFailure.model_validate(report_payload)
            except Exception as e:
                log.warning("agent_b.failure_parse_failed", error=str(e), payload=report_payload)
                return None
        return None
