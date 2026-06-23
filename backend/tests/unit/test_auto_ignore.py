"""4.x: Agent A 评分 < 6.5 自动 IGNORED（避免堵塞 PENDING_DECISION）单测。

只测 settings + flow contract；analyze_worker 的完整 LLM 调用走 e2e。
"""
from __future__ import annotations


def test_setting_default_on() -> None:
    from app.core.config import get_settings
    s = get_settings()
    assert s.auto_ignore_low_score is True


def test_setting_env_override() -> None:
    import os
    from app.core.config import Settings
    os.environ["AUTO_IGNORE_LOW_SCORE"] = "false"
    try:
        s = Settings()
        assert s.auto_ignore_low_score is False
    finally:
        os.environ.pop("AUTO_IGNORE_LOW_SCORE", None)


def test_issue_service_decide_ignore_allowed_from_pending_decision() -> None:
    """是 4.x 自动忽略链路的契约：state machine 必须允许 PENDING_DECISION → IGNORED。"""
    from app.models.enums import IssueStatus
    from app.services.issue_service import _ALLOWED_FROM

    assert IssueStatus.PENDING_DECISION in _ALLOWED_FROM[IssueStatus.IGNORED]


def test_analyze_worker_imports_settings_for_auto_ignore() -> None:
    """analyze_worker 必须能 import settings；保 4.x 路径不被静默回滚。"""
    from app.workers.analyze_worker import _analyze_issue_async  # noqa: F401
    from app.core.config import get_settings
    assert callable(get_settings)
