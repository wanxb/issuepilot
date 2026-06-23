"""Agent B Extended Thinking 切换判定（3.2）单测。"""
from __future__ import annotations

import pytest

from app.sandbox.extended_thinking import (
    parse_difficulties,
    should_enable_extended_thinking,
)


class TestParseDifficulties:
    @pytest.mark.parametrize("raw,expected", [
        ("hard", frozenset({"hard"})),
        ("hard,medium", frozenset({"hard", "medium"})),
        ("HARD, Medium ", frozenset({"hard", "medium"})),
        ("", frozenset()),
        (None, frozenset()),
        (",,,", frozenset()),
    ])
    def test_parse(self, raw: str | None, expected: frozenset[str]) -> None:
        assert parse_difficulties(raw) == expected


class TestShouldEnable:
    def test_difficulty_hard_triggers(self) -> None:
        ok, reason = should_enable_extended_thinking(
            evaluation_difficulty="hard", attempt_number=1,
            enable_difficulties_raw="hard", min_attempt=2,
        )
        assert ok and "difficulty=hard" in reason

    def test_difficulty_case_insensitive(self) -> None:
        ok, _ = should_enable_extended_thinking(
            evaluation_difficulty="HARD", attempt_number=1,
            enable_difficulties_raw="hard", min_attempt=2,
        )
        assert ok

    def test_retry_triggers(self) -> None:
        ok, reason = should_enable_extended_thinking(
            evaluation_difficulty="easy", attempt_number=2,
            enable_difficulties_raw="hard", min_attempt=2,
        )
        assert ok and "attempt=2" in reason

    def test_first_attempt_easy_no_trigger(self) -> None:
        ok, reason = should_enable_extended_thinking(
            evaluation_difficulty="easy", attempt_number=1,
            enable_difficulties_raw="hard", min_attempt=2,
        )
        assert not ok and reason == "default_off"

    def test_min_attempt_zero_disables_retry_path(self) -> None:
        ok, _ = should_enable_extended_thinking(
            evaluation_difficulty="easy", attempt_number=5,
            enable_difficulties_raw="hard", min_attempt=0,
        )
        assert not ok

    def test_no_difficulty_no_attempt_threshold(self) -> None:
        ok, _ = should_enable_extended_thinking(
            evaluation_difficulty=None, attempt_number=1,
            enable_difficulties_raw="hard", min_attempt=2,
        )
        assert not ok

    def test_multiple_difficulties(self) -> None:
        ok, reason = should_enable_extended_thinking(
            evaluation_difficulty="medium", attempt_number=1,
            enable_difficulties_raw="hard,medium", min_attempt=2,
        )
        assert ok and "difficulty=medium" in reason
