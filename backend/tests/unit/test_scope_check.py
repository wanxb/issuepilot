"""Agent B 修改影响范围检查（3.2）单测。"""
from __future__ import annotations

import pytest

from app.sandbox.scope_check import extract_paths, scope_check


class TestExtractPaths:
    def test_extracts_simple_path(self) -> None:
        text = "Please fix src/app.py to handle empty input."
        assert "src/app.py" in extract_paths(text)

    def test_extracts_relative_with_dot(self) -> None:
        text = "Bug is in utils.go on line 42"
        assert "utils.go" in extract_paths(text)

    def test_ignores_short_words(self) -> None:
        text = "Hi"
        assert extract_paths(text) == set()

    def test_ignores_blacklist_terms(self) -> None:
        text = "github.com is fine but e.g. don't match"
        out = extract_paths(text)
        assert "github.com" not in out
        assert "e.g." not in out

    def test_ignores_email(self) -> None:
        text = "Email author@example.com about it"
        out = extract_paths(text)
        # 邮箱 example.com 不应被识别为路径
        assert "example.com" not in out

    def test_none_returns_empty(self) -> None:
        assert extract_paths(None) == set()


class TestScopeCheck:
    def test_suspicious_when_no_overlap(self) -> None:
        out = scope_check(
            files_changed=["src/auth.py", "src/login.py"],
            issue_body="Bug in src/database.py line 12",
            evaluation_summary="数据库相关问题",
        )
        assert out["suspicious"] is True
        assert "src/auth.py" in out["files_changed"]
        assert "src/database.py" in out["referenced_in_issue"]
        assert out["matched"] == []

    def test_not_suspicious_when_basename_matches(self) -> None:
        out = scope_check(
            files_changed=["src/app.py"],
            issue_body="Fix app.py to handle nil",
            evaluation_summary="",
        )
        assert out["suspicious"] is False
        assert "src/app.py" in out["matched"]

    def test_not_suspicious_when_no_reference(self) -> None:
        out = scope_check(
            files_changed=["src/app.py"],
            issue_body="general improvement",
            evaluation_summary="",
        )
        assert out["suspicious"] is False
        assert out["referenced_in_issue"] == []

    def test_not_suspicious_when_no_files(self) -> None:
        out = scope_check(
            files_changed=[],
            issue_body="src/app.py is buggy",
            evaluation_summary="",
        )
        assert out["suspicious"] is False

    def test_basename_case_insensitive(self) -> None:
        out = scope_check(
            files_changed=["src/App.py"],
            issue_body="bug in app.py",
            evaluation_summary="",
        )
        # basename 大小写差异不算 scope_creep
        assert out["suspicious"] is False
        assert out["matched"]

    def test_partial_match_not_suspicious(self) -> None:
        """有任一文件命中 → 不再标记 suspicious。"""
        out = scope_check(
            files_changed=["src/app.py", "src/unrelated.py"],
            issue_body="please fix app.py",
            evaluation_summary="",
        )
        assert out["suspicious"] is False
        assert "src/app.py" in out["matched"]
        assert "src/unrelated.py" not in out["matched"]
