"""parse_github_url 全 case 测试。"""
from __future__ import annotations

import pytest

from app.github.url_parser import (
    InvalidGitHubURLError,
    parse_github_url,
)


class TestRepoURL:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/octocat/hello-world",
            "https://github.com/octocat/hello-world/",
            "http://github.com/octocat/hello-world",
            "github.com/octocat/hello-world",
            "https://www.github.com/octocat/hello-world",
        ],
    )
    def test_repo_variants(self, url: str) -> None:
        p = parse_github_url(url)
        assert p.mode == "repo"
        assert p.owner == "octocat"
        assert p.repo == "hello-world"
        assert p.issue_number is None
        assert p.full_name == "octocat/hello-world"

    def test_repo_with_dot_git(self) -> None:
        p = parse_github_url("https://github.com/owner/my-repo.git")
        assert p.mode == "repo"
        assert p.repo == "my-repo"

    def test_repo_with_special_chars(self) -> None:
        # owner/repo 允许 ., -, _
        p = parse_github_url("https://github.com/some.org/my_repo.v2")
        assert p.owner == "some.org"
        assert p.repo == "my_repo.v2"


class TestIssueURL:
    def test_basic_issue(self) -> None:
        p = parse_github_url("https://github.com/octocat/hello-world/issues/42")
        assert p.mode == "issue"
        assert p.owner == "octocat"
        assert p.repo == "hello-world"
        assert p.issue_number == 42

    def test_issue_trailing_slash(self) -> None:
        p = parse_github_url("https://github.com/o/r/issues/7/")
        assert p.mode == "issue"
        assert p.issue_number == 7


class TestInvalid:
    @pytest.mark.parametrize(
        "url",
        [
            "",
            "  ",
            "https://gitlab.com/owner/repo",
            "https://example.com/foo",
            "https://github.com/",
            "https://github.com/owner",
            "https://github.com/owner/repo/pulls",
            "not a url",
        ],
    )
    def test_rejects(self, url: str) -> None:
        with pytest.raises(InvalidGitHubURLError):
            parse_github_url(url)

    def test_pr_url_rejected_with_hint(self) -> None:
        with pytest.raises(InvalidGitHubURLError, match="PR URL not supported"):
            parse_github_url("https://github.com/octocat/hello/pull/3")
