"""GitHub client 真实 API 集成测试。

默认 skip。通过 `RUN_INTEGRATION_TESTS=1 uv run pytest tests/integration` 启用。

匿名访问 GitHub API 限 60/小时；带 token 限 5000/小时。
本测试只跑 3 次请求，不会撞限。

固定靶标：octocat/Hello-World（GitHub 官方测试仓库，URL 不会变）。
"""
from __future__ import annotations

import os

import pytest

from app.github.client import GitHubClient

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION_TESTS") != "1",
    reason="set RUN_INTEGRATION_TESTS=1 to run",
)


@pytest.mark.asyncio
async def test_get_octocat_hello_world() -> None:
    """真实拉 octocat/Hello-World 元数据，确保字段映射正确。"""
    token = os.environ.get("GITHUB_TOKEN") or None
    async with GitHubClient(token=token) as client:
        repo = await client.get_repo("octocat", "Hello-World")
        assert repo.full_name == "octocat/Hello-World"
        assert repo.owner.login == "octocat"
        # Hello-World 一直存在，star 数稳定
        assert repo.stargazers_count >= 1000
        # rate limit 头被解析
        assert client.last_rate_limit is not None
        assert client.last_rate_limit.limit > 0


@pytest.mark.asyncio
async def test_list_open_issues_octocat() -> None:
    """拉真实仓库的 open issues 列表（max 5）。"""
    token = os.environ.get("GITHUB_TOKEN") or None
    async with GitHubClient(token=token) as client:
        issues = await client.list_open_issues("octocat", "Hello-World", max_count=5)
        # 数量可能为 0；只验证返回类型
        assert isinstance(issues, list)
        for issue in issues:
            assert not issue.is_pull_request
            assert issue.title
            assert issue.html_url.startswith("https://github.com/octocat/Hello-World/")


@pytest.mark.asyncio
async def test_get_languages_octocat() -> None:
    token = os.environ.get("GITHUB_TOKEN") or None
    async with GitHubClient(token=token) as client:
        langs = await client.get_languages("octocat", "Hello-World")
        assert isinstance(langs, dict)
