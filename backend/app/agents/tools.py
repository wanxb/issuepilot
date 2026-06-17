"""Tool 定义（送入 Anthropic Messages API）。

每个 Agent 有且只有一个终止工具，模型必须调用它才算正常结束。
这些 schema 严格对齐 docs/AGENT_DESIGN.md。
"""
from __future__ import annotations

from app.llm.base import ToolDefinition

# ---------------------------------------------------------------------------
# Agent A 终止工具
# ---------------------------------------------------------------------------

EVALUATE_ISSUE_TOOL = ToolDefinition(
    name="evaluate_issue",
    description=(
        "Submit the value evaluation of a GitHub Issue. You MUST call this tool "
        "exactly once to deliver your assessment. Do not return free-form text."
    ),
    input_schema={
        "type": "object",
        "required": [
            "total_score",
            "difficulty",
            "estimated_hours",
            "summary",
            "recommendation",
            "is_worth_developing",
            "dimensions",
        ],
        "properties": {
            "total_score": {
                "type": "number",
                "minimum": 0,
                "maximum": 10,
                "description": "Weighted overall score 0–10.",
            },
            "difficulty": {
                "type": "string",
                "enum": ["easy", "medium", "hard"],
            },
            "estimated_hours": {
                "type": "number",
                "minimum": 0,
                "description": "Estimated AI-developer hours to ship a PR.",
            },
            "summary": {
                "type": "string",
                "description": (
                    "Chinese, 100–150 chars. For the operator to read."
                ),
            },
            "recommendation": {
                "type": "string",
                "description": "Chinese, ≤ 50 chars. Whether to invest dev time.",
            },
            "is_worth_developing": {
                "type": "boolean",
                "description": (
                    "Hard rule: TRUE iff total_score >= 6.5. Do not override "
                    "subjectively."
                ),
            },
            "dimensions": {
                "type": "object",
                "required": [
                    "clarity",
                    "feasibility",
                    "value",
                    "repo_activity",
                    "context_sufficiency",
                ],
                "additionalProperties": {
                    "type": "object",
                    "required": ["score", "comment"],
                    "properties": {
                        "score": {"type": "number", "minimum": 0, "maximum": 10},
                        "comment": {"type": "string"},
                    },
                },
            },
        },
    },
)
