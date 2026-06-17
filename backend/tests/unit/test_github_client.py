"""GitHub client 单元测试（respx mock）。

不依赖网络。
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.github.client import (
    GITHUB_API_BASE,
    GitHubClient,
    RateLimitedError,
    RepoForbiddenError,
    RepoNotFoundError,
)


@pytest.fixture
def client() -> GitHubClient:
    return GitHubClient(token="test-token")


def _fake_repo_payload(full_name: str = "octocat/hello-world") -> dict:
    owner, name = full_name.split("/")
    return {
        "id": 1,
        "name": name,
        "full_name": full_name,
        "owner": {"login": owner, "type": "User"},
        "description": "A test repo",
        "language": "Python",
        "topics": ["test", "demo"],
        "stargazers_count": 42,
        "forks_count": 3,
        "open_issues_count": 5,
        "pushed_at": "2026-06-01T10:00:00Z",
        "default_branch": "main",
    }


def _fake_issue_payload(number: int = 1, *, is_pr: bool = False) -> dict:
    return {
        "id": 100 + number,
        "number": number,
        "title": f"Issue #{number}",
        "body": "bug body",
        "state": "open",
        "html_url": f"https://github.com/octocat/hello-world/issues/{number}",
        "user": {"login": "alice", "type": "User"},
        "labels": [{"name": "bug"}, {"name": "good first issue"}],
        "pull_request": {"url": "..."} if is_pr else None,
        "created_at": "2026-06-01T00:00:00Z",
        "updated_at": "2026-06-02T00:00:00Z",
        "closed_at": None,
    }


class TestGetRepo:
    @respx.mock
    @pytest.mark.asyncio
    async def test_success(self, client: GitHubClient) -> None:
        respx.get(f"{GITHUB_API_BASE}/repos/octocat/hello-world").mock(
            return_value=httpx.Response(200, json=_fake_repo_payload()),
        )
        repo = await client.get_repo("octocat", "hello-world")
        assert repo.full_name == "octocat/hello-world"
        assert repo.language == "Python"
        assert repo.stargazers_count == 42
        assert "test" in repo.topics
        await client.aclose()

    @respx.mock
    @pytest.mark.asyncio
    async def test_404(self, client: GitHubClient) -> None:
        respx.get(f"{GITHUB_API_BASE}/repos/nope/nope").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"}),
        )
        with pytest.raises(RepoNotFoundError):
            await client.get_repo("nope", "nope")
        await client.aclose()


class TestGetIssue:
    @respx.mock
    @pytest.mark.asyncio
    async def test_success(self, client: GitHubClient) -> None:
        respx.get(f"{GITHUB_API_BASE}/repos/o/r/issues/42").mock(
            return_value=httpx.Response(200, json=_fake_issue_payload(42)),
        )
        issue = await client.get_issue("o", "r", 42)
        assert issue.number == 42
        assert issue.title == "Issue #42"
        assert sorted(issue.label_names) == ["bug", "good first issue"]
        assert not issue.is_pull_request
        await client.aclose()

    @respx.mock
    @pytest.mark.asyncio
    async def test_rejects_pr_in_issue_endpoint(self, client: GitHubClient) -> None:
        # GH issues 接口同时返回 PR，URL 是 issue 路径但是 payload.pull_request != None
        respx.get(f"{GITHUB_API_BASE}/repos/o/r/issues/7").mock(
            return_value=httpx.Response(200, json=_fake_issue_payload(7, is_pr=True)),
        )
        from app.github.client import GitHubError

        with pytest.raises(GitHubError, match="pull request"):
            await client.get_issue("o", "r", 7)
        await client.aclose()


class TestListOpenIssues:
    @respx.mock
    @pytest.mark.asyncio
    async def test_paginates_and_skips_prs(self, client: GitHubClient) -> None:
        # 第一页：返回 5 条，其中 2 个是 PR
        page1 = [_fake_issue_payload(n, is_pr=(n in (3, 4))) for n in range(1, 6)]
        respx.get(f"{GITHUB_API_BASE}/repos/o/r/issues").mock(
            return_value=httpx.Response(200, json=page1),
        )
        issues = await client.list_open_issues("o", "r", max_count=10)
        # PR 被过滤
        assert len(issues) == 3
        assert [i.number for i in issues] == [1, 2, 5]
        await client.aclose()

    @respx.mock
    @pytest.mark.asyncio
    async def test_caps_at_max_count(self, client: GitHubClient) -> None:
        page = [_fake_issue_payload(n) for n in range(1, 11)]
        respx.get(f"{GITHUB_API_BASE}/repos/o/r/issues").mock(
            return_value=httpx.Response(200, json=page),
        )
        issues = await client.list_open_issues("o", "r", max_count=3)
        assert len(issues) == 3
        await client.aclose()


class TestRateLimit:
    @respx.mock
    @pytest.mark.asyncio
    async def test_403_with_remaining_zero_raises_rate_limited(
        self, client: GitHubClient,
    ) -> None:
        respx.get(f"{GITHUB_API_BASE}/repos/o/r").mock(
            return_value=httpx.Response(
                403,
                json={"message": "API rate limit exceeded"},
                headers={
                    "X-RateLimit-Limit": "60",
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": "1700000000",
                    "Retry-After": "120",
                },
            ),
        )
        with pytest.raises(RateLimitedError) as exc:
            await client.get_repo("o", "r")
        assert exc.value.retry_after_seconds == 120
        assert client.last_rate_limit is not None
        assert client.last_rate_limit.remaining == 0
        await client.aclose()

    @respx.mock
    @pytest.mark.asyncio
    async def test_403_with_remaining_nonzero_is_forbidden(
        self, client: GitHubClient,
    ) -> None:
        respx.get(f"{GITHUB_API_BASE}/repos/o/r").mock(
            return_value=httpx.Response(
                403,
                json={"message": "permission denied"},
                headers={"X-RateLimit-Remaining": "59"},
            ),
        )
        with pytest.raises(RepoForbiddenError):
            await client.get_repo("o", "r")
        await client.aclose()


class TestRecentMergedPRs:
    @respx.mock
    @pytest.mark.asyncio
    async def test_filters_unmerged_and_caps(self, client: GitHubClient) -> None:
        now = datetime(2026, 6, 1, tzinfo=timezone.utc).isoformat()
        payload = [
            # merged
            {"id": 1, "number": 10, "title": "a", "state": "closed",
             "html_url": "x", "merged_at": now, "closed_at": now, "user": None},
            # closed, not merged
            {"id": 2, "number": 11, "title": "b", "state": "closed",
             "html_url": "x", "merged_at": None, "closed_at": now, "user": None},
            # merged
            {"id": 3, "number": 12, "title": "c", "state": "closed",
             "html_url": "x", "merged_at": now, "closed_at": now, "user": None},
        ]
        respx.get(f"{GITHUB_API_BASE}/repos/o/r/pulls").mock(
            return_value=httpx.Response(200, json=payload),
        )
        prs = await client.list_recent_merged_prs("o", "r", count=5)
        assert [p.number for p in prs] == [10, 12]
        await client.aclose()
