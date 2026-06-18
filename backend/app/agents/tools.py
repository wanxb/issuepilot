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


# ---------------------------------------------------------------------------
# Agent C 终止工具
# ---------------------------------------------------------------------------

SUBMIT_REVIEW_TOOL = ToolDefinition(
    name="submit_review",
    description=(
        "Submit the code review verdict for the Agent B diff. You MUST call this "
        "tool exactly once. When verdict is APPROVED, pr_title and pr_body MUST "
        "be provided (English). When REJECTED, rejection_reason MUST be provided "
        "with concrete file names + line numbers + suggested change."
    ),
    input_schema={
        "type": "object",
        "required": [
            "verdict",
            "overall_score",
            "dimensions",
            "overall_comment",
        ],
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["APPROVED", "REJECTED"],
            },
            "overall_score": {
                "type": "number",
                "minimum": 0,
                "maximum": 10,
            },
            "dimensions": {
                "type": "object",
                "required": [
                    "correctness",
                    "test_coverage",
                    "code_style",
                    "security",
                    "pr_description",
                ],
                "additionalProperties": {
                    "type": "object",
                    "required": ["score", "passed", "comment"],
                    "properties": {
                        "score": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 10,
                        },
                        "passed": {"type": "boolean"},
                        "comment": {"type": "string"},
                    },
                },
            },
            "rejection_reason": {
                "type": ["string", "null"],
                "description": (
                    "Required when verdict=REJECTED. Must include file path, "
                    "line numbers, and concrete change suggestion. No vague "
                    "statements like 'code has issues'."
                ),
            },
            "pr_title": {
                "type": ["string", "null"],
                "description": "Required when verdict=APPROVED. English imperative.",
            },
            "pr_body": {
                "type": ["string", "null"],
                "description": "Required when verdict=APPROVED. English Markdown.",
            },
            "overall_comment": {
                "type": "string",
                "description": "≤200 chars, Chinese, internal summary.",
            },
        },
    },
)


# ---------------------------------------------------------------------------
# Agent D 终止工具
# ---------------------------------------------------------------------------

REPORT_PROFILE_TOOL = ToolDefinition(
    name="report_profile",
    description=(
        "Submit the repository onboarding profile (test/install commands, "
        "code style notes, contributing summary, merged PR examples). You MUST "
        "call this tool exactly once. profile_quality=low is acceptable but "
        "you still MUST provide best-effort test_command / install_command — "
        "do NOT refuse to output."
    ),
    input_schema={
        "type": "object",
        "required": [
            "test_command",
            "install_command",
            "code_style_notes",
            "contributing_summary",
            "forbidden_patterns",
            "pr_title_convention",
            "merged_pr_examples",
            "profile_quality",
            "quality_reason",
        ],
        "properties": {
            "test_command":    {"type": "string"},
            "install_command": {"type": "string"},
            "lint_command":    {"type": ["string", "null"]},
            "code_style_notes": {
                "type": "string",
                "maxLength": 500,
                "description": "≤500 chars summary of naming/indent/comment conventions.",
            },
            "contributing_summary": {
                "type": "string",
                "maxLength": 500,
                "description": "≤500 chars summary of CONTRIBUTING.md key requirements.",
            },
            "forbidden_patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Patterns to AVOID, e.g. 'do not modify generated/', "
                    "'do not add new dependencies'."
                ),
            },
            "pr_title_convention": {
                "type": "string",
                "description": "e.g. 'fix(scope): summary' or '[BUG] ...'",
            },
            "merged_pr_examples": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "required": ["url", "title_pattern", "diff_style_note"],
                    "properties": {
                        "url":             {"type": "string"},
                        "title_pattern":   {"type": "string"},
                        "diff_style_note": {"type": "string"},
                    },
                },
            },
            "profile_quality": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
            "quality_reason": {
                "type": "string",
                "maxLength": 200,
            },
        },
    },
)
