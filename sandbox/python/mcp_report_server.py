#!/usr/bin/env python3
"""
MCP stdio server: 暴露 Agent B 的两个终止工具

    - report_completion: 任务成功完成时调用
    - report_failure:    遇到无法解决障碍时调用

设计来源：docs/AGENT_DESIGN.md §Agent B Tool Definitions

约束：
    - 工具被调用时，将参数写入 /workspace/.agent_report.json 供调度器读取
    - 写入后返回简短确认，让模型自然结束循环
    - 严格 JSON Schema 校验（pydantic），不符合时返回明确错误让模型重试

实现：使用 MCP 1.x Python SDK 的 stdio 传输。Claude Code CLI 通过 ~/.claude.json
的 mcpServers 字段或 --mcp-config 加载此 server。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ValidationError

REPORT_PATH = Path(os.environ.get("AGENT_REPORT_PATH", "/workspace/.agent_report.json"))

mcp = FastMCP("issuepilot-agent-b-terminator")


# ---------------------------------------------------------------------------
# 输出 Schema —— 与 docs/AGENT_DESIGN.md 严格对齐
# ---------------------------------------------------------------------------

class CompletionReport(BaseModel):
    success: bool
    files_changed: list[str]
    diff_summary: str = Field(max_length=200)
    test_passed: bool
    total_tests: int = Field(ge=0)
    failed_tests: int = Field(ge=0)
    new_tests_added: int = Field(ge=0)
    test_output_snippet: str = Field(max_length=500)


class FailureReport(BaseModel):
    reason: Literal[
        "cannot_locate_issue",
        "requires_external_deps",
        "issue_is_invalid",
        "test_infrastructure",
        "out_of_scope",
    ]
    detail: str = Field(min_length=20)


def _write_report(kind: str, payload: dict[str, Any]) -> None:
    REPORT_PATH.write_text(
        json.dumps({"kind": kind, "payload": payload}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Tool: report_completion
# ---------------------------------------------------------------------------

@mcp.tool()
def report_completion(
    success: bool,
    files_changed: list[str],
    diff_summary: str,
    test_passed: bool,
    total_tests: int,
    failed_tests: int,
    new_tests_added: int,
    test_output_snippet: str,
) -> str:
    """Report that the development task is complete.

    Call this tool exactly once when you have finished implementing the change,
    run the test suite, and committed your work. After this call, your loop ends.
    """
    try:
        report = CompletionReport(
            success=success,
            files_changed=files_changed,
            diff_summary=diff_summary,
            test_passed=test_passed,
            total_tests=total_tests,
            failed_tests=failed_tests,
            new_tests_added=new_tests_added,
            test_output_snippet=test_output_snippet,
        )
    except ValidationError as e:
        return f"VALIDATION_ERROR: {e.errors()}"

    _write_report("completion", report.model_dump())
    return "OK: completion report saved. You may stop now."


# ---------------------------------------------------------------------------
# Tool: report_failure
# ---------------------------------------------------------------------------

@mcp.tool()
def report_failure(reason: str, detail: str) -> str:
    """Report that the task cannot be completed.

    Call this tool when you hit a blocker you cannot resolve (e.g. the issue
    requires external services, the repository's test infrastructure is broken,
    or the task is out of your scope). After this call, your loop ends.
    """
    try:
        report = FailureReport(reason=reason, detail=detail)
    except ValidationError as e:
        return f"VALIDATION_ERROR: {e.errors()}"

    _write_report("failure", report.model_dump())
    return "OK: failure report saved. You may stop now."


if __name__ == "__main__":
    # FastMCP 的 run() 默认以 stdio transport 启动
    mcp.run()
