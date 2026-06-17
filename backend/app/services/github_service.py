"""GitHubService —— Agent B 所需的 GitHub 操作（fork + branch 命名）。

职责：
    - fork_repo: 用 dev token fork 目标仓库（如未配置 dev token 则直接返回原 repo）
    - generate_branch_name: 生成确定性分支名 issuepilot/issue-{number}-{slug}

注意：
    - fork API 是异步的（GitHub 后台执行），返回时 fork 可能还没有完全就绪
    - 调用方（dev_worker）应在 clone 前给一定等待时间（约 3-5s）
"""
from __future__ import annotations

import re

import httpx
import structlog

from app.core.config import get_settings

log = structlog.get_logger(__name__)

GITHUB_API_BASE = "https://api.github.com"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class GitHubServiceError(Exception):
    pass


class GitHubService:
    def __init__(self, *, dev_token: str | None = None, token: str | None = None) -> None:
        self._dev_token = dev_token
        self._token = token

    @classmethod
    def from_settings(cls) -> "GitHubService":
        s = get_settings()
        return cls(
            dev_token=s.github_dev_token.get_secret_value() if s.github_dev_token else None,
            token=s.github_token.get_secret_value() if s.github_token else None,
        )

    # ---- fork ----

    async def fork_repo(self, repo_full_name: str) -> str:
        """Fork 目标仓库，返回 forked full_name。

        - 如果配置了 github_dev_token：fork 到 dev 账号，返回 fork full_name
        - 如果只有 github_token：直接返回原 repo（skip fork）
        - fork 已存在时 GitHub 返回现有 fork（幂等）
        """
        if not self._dev_token:
            log.info("github_service.fork_skipped", reason="no_dev_token", repo=repo_full_name)
            return repo_full_name

        owner, repo = repo_full_name.split("/", 1)
        headers = {
            "Authorization": f"Bearer {self._dev_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(base_url=GITHUB_API_BASE, timeout=30.0) as client:
            resp = await client.post(
                f"/repos/{owner}/{repo}/forks",
                headers=headers,
                json={},
            )
            if resp.status_code not in (200, 202):
                raise GitHubServiceError(
                    f"fork failed: {resp.status_code} {resp.text[:200]}"
                )
            fork_data = resp.json()
            forked_full_name: str = fork_data["full_name"]

        log.info("github_service.forked", original=repo_full_name, fork=forked_full_name)
        return forked_full_name

    # ---- branch ----

    @staticmethod
    def generate_branch_name(issue_number: int, issue_title: str) -> str:
        """issuepilot/issue-{number}-{slug}  (slug ≤ 30 chars, lowercase)"""
        slug = _SLUG_RE.sub("-", issue_title.lower()).strip("-")
        slug = slug[:30].rstrip("-")
        if not slug:
            slug = "fix"
        return f"issuepilot/issue-{issue_number}-{slug}"
