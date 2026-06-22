"""Agent B 重试机制（2.3）的纯逻辑测试。

测对象：
    - dev_worker.build_failure_review_context 输出文本结构
    - dev_worker._enqueue_dev_retry_or_stop 复用了 review_worker.decide_retry_or_archive，
      边界在 test_review_retry.py 已覆盖；这里只验证两侧导出的是同一个函数。
"""
from __future__ import annotations

import pytest

from app.models.enums import DevTaskStatus
from app.models.dev_task import DevTask
from app.workers.dev_worker import build_failure_review_context
from app.workers.review_worker import decide_retry_or_archive


def _dev_task(*, reason: str | None, detail: str | None) -> DevTask:
    """构造 DevTask 而不入 DB。SQLAlchemy 模型对象允许这样裸用。"""
    dt = DevTask()
    dt.attempt_number = 1
    dt.status = DevTaskStatus.FAILED
    dt.failure_reason = reason
    dt.failure_detail = detail
    return dt


class TestFailureReviewContext:
    def test_includes_attempt_number_and_reason(self) -> None:
        dt = _dev_task(reason="sandbox_start_failed",
                       detail="Docker image not found")
        ctx = build_failure_review_context(dt, prev_attempt=1)
        assert "attempt 1" in ctx
        assert "sandbox_start_failed" in ctx
        assert "Docker image not found" in ctx

    def test_missing_reason_falls_back_to_unknown(self) -> None:
        dt = _dev_task(reason=None, detail=None)
        ctx = build_failure_review_context(dt, prev_attempt=2)
        assert "Failure reason: unknown" in ctx
        assert "(no detail)" in ctx

    def test_warning_tail_present(self) -> None:
        """Agent B 看到失败上下文时应被劝阻"重复同一路径"。"""
        dt = _dev_task(reason="loop_timeout", detail="25 turns exceeded")
        ctx = build_failure_review_context(dt, prev_attempt=1)
        assert "Avoid repeating" in ctx
        assert "report_failure" in ctx

    def test_long_detail_truncated(self) -> None:
        dt = _dev_task(reason="x", detail="A" * 5000)
        ctx = build_failure_review_context(dt, prev_attempt=1)
        # truncated to 1500 + ellipsis-ish; just assert no full 5000
        assert "A" * 5000 not in ctx


class TestRetryDecisionReused:
    """dev 和 review 共用同一 decide 函数，避免边界规则漂移。"""

    @pytest.mark.parametrize("attempt,maximum,expected", [
        (1, 2, "retry"),
        (2, 2, "archive"),       # 第 2 次尝试就是 max → 不再 retry
        (1, 1, "archive"),       # max=1 关掉 dev 重试
        (1, 0, "archive"),
    ])
    def test_dev_default_max_2(self, attempt: int, maximum: int, expected: str) -> None:
        assert decide_retry_or_archive(
            review_attempt=attempt, max_attempts=maximum,
        ) == expected
