"""PRTracker.on_* 触发 rejection_reasons 占位写入的纯逻辑测试（2.3）。

不连 DB：用一个最小的 fake session 捕获 add() 的对象，断言写出的
RejectionReason 字段与预期一致。

完整 webhook → DB 端到端覆盖在 test_webhook_api.py（host 端 docker exec
范式跑）+ scripts/replay_github_webhook.py 验证。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from app.models.enums import (
    AgentBAttribution,
    RejectionCategory,
    RejectionSeverity,
    RejectionSource,
)
from app.models.pull_request import PullRequest
from app.models.rejection_reason import RejectionReason
from app.services.pr_tracker import PRTracker


class _FakeSession:
    """只实现 PRTracker 调用到的两个方法。"""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass

    async def refresh(self, obj: Any, attrs: list[str]) -> None:
        pass

    async def execute(self, stmt: Any) -> Any:  # pragma: no cover (unused)
        raise NotImplementedError


def _fake_pr() -> PullRequest:
    pr = PullRequest()
    pr.id = uuid.uuid4()
    pr.issue_id = uuid.uuid4()
    return pr


def _rejections(s: _FakeSession) -> list[RejectionReason]:
    return [o for o in s.added if isinstance(o, RejectionReason)]


@pytest.mark.asyncio
class TestOnReviewReceived:
    async def test_approved_writes_no_rejection(self) -> None:
        s = _FakeSession()
        tracker = PRTracker(s)  # type: ignore[arg-type]
        await tracker.on_review_received(
            _fake_pr(), reviewer="alice", state="approved",
            body="LGTM", occurred_at=datetime.now(timezone.utc),
        )
        assert _rejections(s) == [], "approved review 不应入 rejection_reasons"

    async def test_changes_requested_writes_rejection(self) -> None:
        s = _FakeSession()
        tracker = PRTracker(s)  # type: ignore[arg-type]
        await tracker.on_review_received(
            _fake_pr(), reviewer="bob", state="changes_requested",
            body="please add tests", occurred_at=datetime.now(timezone.utc),
        )
        rs = _rejections(s)
        assert len(rs) == 1
        r = rs[0]
        assert r.source == RejectionSource.MAINTAINER_REVIEW
        assert r.classified_by is None             # 等 classifier
        assert r.agent_b_attribution == AgentBAttribution.UNCLEAR
        assert r.category == RejectionCategory.OTHER
        assert r.severity == RejectionSeverity.MINOR
        assert r.raw_text == "please add tests"
        assert "changes_requested" in r.detail

    async def test_commented_with_body_writes_rejection(self) -> None:
        s = _FakeSession()
        tracker = PRTracker(s)  # type: ignore[arg-type]
        await tracker.on_review_received(
            _fake_pr(), reviewer="carol", state="commented",
            body="一些建议", occurred_at=datetime.now(timezone.utc),
        )
        assert len(_rejections(s)) == 1

    async def test_empty_body_skipped(self) -> None:
        s = _FakeSession()
        tracker = PRTracker(s)  # type: ignore[arg-type]
        await tracker.on_review_received(
            _fake_pr(), reviewer="dave", state="changes_requested",
            body="   ", occurred_at=datetime.now(timezone.utc),
        )
        assert _rejections(s) == [], "空 body 不写"


@pytest.mark.asyncio
class TestOnCommentReceived:
    async def test_non_empty_writes_rejection(self) -> None:
        s = _FakeSession()
        tracker = PRTracker(s)  # type: ignore[arg-type]
        await tracker.on_comment_received(
            _fake_pr(), commenter="eve", body="ping?",
            occurred_at=datetime.now(timezone.utc),
        )
        rs = _rejections(s)
        assert len(rs) == 1
        assert rs[0].source == RejectionSource.MAINTAINER_REVIEW
        assert "pr_comment" in rs[0].detail

    async def test_empty_body_skipped(self) -> None:
        s = _FakeSession()
        tracker = PRTracker(s)  # type: ignore[arg-type]
        await tracker.on_comment_received(
            _fake_pr(), commenter="frank", body="",
            occurred_at=datetime.now(timezone.utc),
        )
        assert _rejections(s) == []
