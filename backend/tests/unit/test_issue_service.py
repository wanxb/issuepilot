"""IssueService 状态机测试（pure logic，不连 DB）。

只校验白名单语义，不测 SQL 行为。
"""
from __future__ import annotations

import pytest

from app.models.enums import IssueStatus
from app.services.issue_service import _ALLOWED_FROM


class TestStateMachineWhitelist:
    def test_analyzing_only_from_discovered(self) -> None:
        assert _ALLOWED_FROM[IssueStatus.ANALYZING] == {IssueStatus.DISCOVERED}

    def test_pending_decision_only_from_analyzing(self) -> None:
        assert _ALLOWED_FROM[IssueStatus.PENDING_DECISION] == {IssueStatus.ANALYZING}

    def test_queued_dev_has_4_entry_points(self) -> None:
        assert _ALLOWED_FROM[IssueStatus.QUEUED_DEV] == {
            IssueStatus.PENDING_DECISION,
            IssueStatus.REVIEW_REJECTED,
            IssueStatus.DEV_FAILED,
            IssueStatus.PR_CLOSED,
        }

    def test_archived_terminal_only(self) -> None:
        assert IssueStatus.ARCHIVED not in {
            v for vs in _ALLOWED_FROM.values() for v in vs
        }, "ARCHIVED must be terminal (no outgoing transitions)"

    @pytest.mark.parametrize(
        "to_state", [s for s in IssueStatus if s != IssueStatus.DISCOVERED],
    )
    def test_every_non_initial_state_has_allowed_from(self, to_state: IssueStatus) -> None:
        assert to_state in _ALLOWED_FROM
        assert _ALLOWED_FROM[to_state], f"{to_state} has empty allowed_from"
