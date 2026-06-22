"""PR 关闭后人工决定 schema 校验（2.3）。

完整 HTTP/DB e2e 在 tests/integration/test_decide_api.py 范式（host docker exec），
本文件只测 PRClosedDecideRequest 字段约束。
"""
from __future__ import annotations

import pytest

from app.schemas.decide import PRClosedDecideRequest


class TestPRClosedDecideSchema:
    @pytest.mark.parametrize("action", ["restart_dev", "archive"])
    def test_valid_actions(self, action: str) -> None:
        r = PRClosedDecideRequest.model_validate({"action": action})
        assert r.action == action

    @pytest.mark.parametrize("action", ["", "ignore", "start_dev", "merge", "foo"])
    def test_invalid_actions_rejected(self, action: str) -> None:
        with pytest.raises(Exception):
            PRClosedDecideRequest.model_validate({"action": action})

    def test_missing_action_rejected(self) -> None:
        with pytest.raises(Exception):
            PRClosedDecideRequest.model_validate({})
