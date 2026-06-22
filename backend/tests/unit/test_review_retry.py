"""Agent C → B 退回循环（2.3）的纯逻辑测试。

只覆盖纯函数 / 配置常量：
    - decide_retry_or_archive 边界
    - build_review_context 文本结构
    - IssueService._ALLOWED_FROM 新增 REVIEW_REJECTED → ARCHIVED

review_worker 主流程（DB + Celery）走集成测试或回归 dry run，本文件不复刻。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.agents.schemas import AgentCOutput
from app.models.enums import IssueStatus
from app.services.issue_service import _ALLOWED_FROM
from app.workers.review_worker import (
    build_review_context,
    decide_retry_or_archive,
)


# ---------------------------------------------------------------------------
# decide_retry_or_archive
# ---------------------------------------------------------------------------


class TestRetryDecision:
    @pytest.mark.parametrize(
        "attempt,maximum,expected",
        [
            (1, 3, "retry"),
            (2, 3, "retry"),
            (3, 3, "archive"),
            (4, 3, "archive"),     # 异常状态也走 archive
            (1, 1, "archive"),     # max=1 表示评一次就算完
            (1, 0, "archive"),     # max=0 = 关掉退回循环
            (1, -5, "archive"),    # 负数兜底
        ],
    )
    def test_boundary(self, attempt: int, maximum: int, expected: str) -> None:
        assert decide_retry_or_archive(
            review_attempt=attempt, max_attempts=maximum,
        ) == expected


# ---------------------------------------------------------------------------
# build_review_context
# ---------------------------------------------------------------------------


def _rejected_output(**overrides: Any) -> AgentCOutput:
    base = {
        "verdict": "REJECTED",
        "overall_score": 4.5,
        "dimensions": {
            "correctness": {"score": 4, "passed": False, "comment": "wrong direction"},
            "test_coverage": {"score": 6, "passed": True, "comment": "tests present"},
            "code_style": {"score": 7, "passed": True, "comment": "ok"},
            "security": {"score": 8, "passed": True, "comment": "no concerns"},
            "pr_description": {"score": 5, "passed": True, "comment": "ok"},
        },
        "rejection_reason": "src/calc.py:12 swap operands; should be b-a not a-b.",
        "pr_title": None,
        "pr_body": None,
        "overall_comment": "方向反了。",
    }
    base.update(overrides)
    return AgentCOutput.model_validate(base)


class TestBuildReviewContext:
    def test_includes_attempt_number(self) -> None:
        out = _rejected_output()
        ctx = build_review_context(out, attempt_number=2)
        assert "attempt 2" in ctx
        assert "REJECTED" in ctx
        assert "4.50" in ctx

    def test_lists_only_failing_dimensions(self) -> None:
        out = _rejected_output()
        ctx = build_review_context(out, attempt_number=1)
        assert "correctness (score 4): wrong direction" in ctx
        # 通过的维度不应再次罗列，避免噪音
        assert "test_coverage" not in ctx
        assert "code_style" not in ctx

    def test_includes_rejection_reason_and_overall_comment(self) -> None:
        out = _rejected_output()
        ctx = build_review_context(out, attempt_number=1)
        assert "src/calc.py:12" in ctx
        assert "方向反了" in ctx

    def test_no_failing_dims_handled_gracefully(self) -> None:
        """理论上不应发生（REJECTED 必有失败维度），但兜底不能崩。"""
        all_passed = {
            "verdict": "REJECTED",
            "overall_score": 5.0,
            "dimensions": {
                k: {"score": 6, "passed": True, "comment": "ok"}
                for k in ("correctness", "test_coverage", "code_style",
                          "security", "pr_description")
            },
            "rejection_reason": "policy violation per maintainer",
            "pr_title": None, "pr_body": None,
            "overall_comment": "no",
        }
        out = AgentCOutput.model_validate(all_passed)
        ctx = build_review_context(out, attempt_number=1)
        assert "Failing dimensions" not in ctx
        assert "policy violation" in ctx

    def test_instruction_tail_always_present(self) -> None:
        """提醒 Agent B 保留正确部分、只改被指出的点。"""
        out = _rejected_output()
        ctx = build_review_context(out, attempt_number=1)
        assert "Keep the parts" in ctx
        assert "Only revise what was flagged" in ctx


# ---------------------------------------------------------------------------
# 状态机白名单
# ---------------------------------------------------------------------------


class TestArchiveTransition:
    def test_review_rejected_can_archive(self) -> None:
        assert IssueStatus.REVIEW_REJECTED in _ALLOWED_FROM[IssueStatus.ARCHIVED]

    def test_review_rejected_can_still_re_queue_dev(self) -> None:
        # 关键不回归：1.5b 时已允许 REVIEW_REJECTED → QUEUED_DEV，
        # 2.3 加 ARCHIVED 出口不能挤掉它。
        assert IssueStatus.REVIEW_REJECTED in _ALLOWED_FROM[IssueStatus.QUEUED_DEV]

    def test_archived_still_terminal(self) -> None:
        outgoing = [
            s for s, allowed in _ALLOWED_FROM.items()
            if IssueStatus.ARCHIVED in allowed
        ]
        assert outgoing == [], (
            "ARCHIVED 仍必须是终态，不可作为其他状态的 from。"
        )
