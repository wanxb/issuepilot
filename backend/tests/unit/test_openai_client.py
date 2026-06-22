"""OpenAIClient 翻译层单测（2.5）。

不发真实网络请求；只测：
    - _tool_to_openai：ToolDefinition (Anthropic schema) → OpenAI function
    - _translate_response：OpenAI message → Anthropic content blocks
    - _map_finish_reason：finish_reason → stop_reason
    - factory 支持 provider=openai / deepseek_openai
"""
from __future__ import annotations

import pytest

from app.agents.tools import EVALUATE_ISSUE_TOOL
from app.llm.openai_client import (
    _map_finish_reason,
    _tool_to_openai,
    _translate_response,
)


class TestToolTranslation:
    def test_anthropic_to_openai(self) -> None:
        out = _tool_to_openai(EVALUATE_ISSUE_TOOL)
        assert out["type"] == "function"
        assert out["function"]["name"] == "evaluate_issue"
        assert out["function"]["description"]
        # parameters 即 Anthropic 的 input_schema（同 JSON Schema 子集）
        assert out["function"]["parameters"] == EVALUATE_ISSUE_TOOL.input_schema


class TestResponseTranslation:
    def test_text_only(self) -> None:
        msg = {"role": "assistant", "content": "hello"}
        blocks = _translate_response(msg)
        assert blocks == [{"type": "text", "text": "hello"}]

    def test_tool_call_with_text(self) -> None:
        msg = {
            "role": "assistant",
            "content": "thinking...",
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "evaluate_issue", "arguments": '{"a":1,"b":2}'},
            }],
        }
        blocks = _translate_response(msg)
        assert len(blocks) == 2
        assert blocks[0]["type"] == "text"
        assert blocks[1]["type"] == "tool_use"
        assert blocks[1]["name"] == "evaluate_issue"
        assert blocks[1]["input"] == {"a": 1, "b": 2}
        assert blocks[1]["id"] == "call_1"

    def test_tool_call_only_no_text(self) -> None:
        msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "x", "type": "function",
                "function": {"name": "t", "arguments": "{}"},
            }],
        }
        blocks = _translate_response(msg)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "tool_use"

    def test_arguments_object_form_compat(self) -> None:
        """DeepSeek 偶尔直接返回 arguments=dict 而非 JSON string；兼容。"""
        msg = {
            "role": "assistant",
            "tool_calls": [{
                "id": "x", "type": "function",
                "function": {"name": "t", "arguments": {"k": "v"}},
            }],
        }
        blocks = _translate_response(msg)
        assert blocks[0]["input"] == {"k": "v"}

    def test_arguments_malformed_json_keeps_raw(self) -> None:
        msg = {
            "role": "assistant",
            "tool_calls": [{
                "id": "x", "type": "function",
                "function": {"name": "t", "arguments": "{not json"},
            }],
        }
        blocks = _translate_response(msg)
        assert "_raw_arguments" in blocks[0]["input"]

    def test_empty_content_no_block(self) -> None:
        """content="" 不该产生空 text 块。"""
        msg = {"role": "assistant", "content": "   "}
        blocks = _translate_response(msg)
        assert blocks == []


class TestFinishReasonMap:
    @pytest.mark.parametrize("oai,anthropic", [
        ("stop", "end_turn"),
        ("tool_calls", "tool_use"),
        ("function_call", "tool_use"),
        ("length", "max_tokens"),
        ("content_filter", "stop_sequence"),
        ("", "end_turn"),
        ("custom_value", "custom_value"),  # 未知值透传
    ])
    def test_mapping(self, oai: str, anthropic: str) -> None:
        assert _map_finish_reason(oai) == anthropic


class TestFactoryDispatch:
    def test_openai_provider_routes_to_openai_client(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENAI_TEST_KEY", "sk-test")
        from app.llm.factory import _build_client
        from app.llm.openai_client import OpenAIClient

        cli = _build_client(
            {
                "provider": "openai", "model": "gpt-4o",
                "api_key_env": "OPENAI_TEST_KEY",
            },
            is_fallback=False,
        )
        assert isinstance(cli, OpenAIClient)
        assert cli.provider == "openai"

    def test_deepseek_openai_uses_label(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DEEPSEEK_TEST_KEY", "sk-test")
        from app.llm.factory import _build_client
        from app.llm.openai_client import OpenAIClient

        cli = _build_client(
            {
                "provider": "deepseek_openai",
                "provider_label": "deepseek",
                "model": "deepseek-chat",
                "api_key_env": "DEEPSEEK_TEST_KEY",
                "base_url": "https://api.deepseek.com/v1",
            },
            is_fallback=False,
        )
        assert isinstance(cli, OpenAIClient)
        # llm_call_logs 写 "deepseek" 让统计与 Anthropic-compat 路径合并
        assert cli.provider == "deepseek"
