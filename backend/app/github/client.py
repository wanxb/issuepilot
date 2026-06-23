"""GitHub REST 客户端（httpx async）。

只暴露 IssuePilot 用到的端点：
    - get_repo(owner, repo)
    - get_issue(owner, repo, number)
    - list_open_issues(owner, repo, max_count=50, labels=None)
    - list_recent_merged_prs(owner, repo, count=10)
    - get_languages(owner, repo)
    - get_rate_limit()

设计：
    - 单例 AsyncClient（连接池复用），生命周期挂到 FastAPI lifespan
    - 401/404/410 抛 GitHubError；429/secondary rate limit 抛 RateLimitedError
    - 调用方负责重试与退避；这一层只做 HTTP I/O
"""
from __future__ import annotations

from typing import Any, Self

import httpx

from app.core.config import get_settings
from app.github.types import (
    GHIssue,
    GHPullRequest,
    GHRepo,
    RateLimit,
)

GITHUB_API_BASE = "https://api.github.com"
ACCEPT = "application/vnd.github+json"
API_VERSION = "2022-11-28"


class GitHubError(Exception):
    """GitHub API 调用通用错误。"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RepoNotFoundError(GitHubError):
    """仓库不存在或不可见（404）。"""


class RepoForbiddenError(GitHubError):
    """403——通常是 token 权限不足或私有仓库。"""


class RateLimitedError(GitHubError):
    """429 / 403 + X-RateLimit-Remaining=0。"""

    def __init__(self, message: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(message, status_code=429)
        self.retry_after_seconds = retry_after_seconds


class GitHubClient:
    """异步 GitHub REST 客户端。"""

    def __init__(
        self,
        token: str | None = None,
        *,
        base_url: str = GITHUB_API_BASE,
        timeout: float = 30.0,
    ) -> None:
        headers = {
            "Accept": ACCEPT,
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "IssuePilot/0.1",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
        )
        self.last_rate_limit: RateLimit | None = None

    @classmethod
    def from_settings(cls) -> Self:
        s = get_settings()
        token = s.github_token.get_secret_value() if s.github_token else None
        return cls(token=token)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ----- 端点封装 -----

    async def get_repo(self, owner: str, repo: str) -> GHRepo:
        data = await self._get(f"/repos/{owner}/{repo}")
        return GHRepo.model_validate(data)

    async def get_issue(self, owner: str, repo: str, number: int) -> GHIssue:
        data = await self._get(f"/repos/{owner}/{repo}/issues/{number}")
        if data.get("pull_request") is not None:
            raise GitHubError(
                f"{owner}/{repo}#{number} is a pull request, not an issue",
                status_code=400,
            )
        return GHIssue.model_validate(data)

    async def search_repositories(
        self,
        *,
        q: str,
        sort: str = "stars",
        order: str = "desc",
        per_page: int = 30,
        max_results: int = 30,
    ) -> list[dict]:
        """GitHub Search API：/search/repositories?q=...&sort=...

        返回 GitHub 原始 repo item 列表（含 owner.login, name, full_name,
        stargazers_count, pushed_at, archived, disabled, topics, ...）。

        注意限流：unauthenticated 60/hr，authenticated 5000/hr。Search 单独
        有 30 req/min 的 secondary rate limit。
        """
        items: list[dict] = []
        page = 1
        per_page = min(100, max(1, per_page))
        while len(items) < max_results:
            data = await self._get(
                "/search/repositories",
                params={
                    "q": q, "sort": sort, "order": order,
                    "per_page": per_page, "page": page,
                },
            )
            chunk = (data or {}).get("items") or []
            if not chunk:
                break
            items.extend(chunk)
            if len(chunk) < per_page:
                break
            page += 1
        return items[:max_results]

    async def list_open_issues(
        self,
        owner: str,
        repo: str,
        *,
        max_count: int = 50,
        labels: list[str] | None = None,
    ) -> list[GHIssue]:
        """拉取 open issues，按 newest 排序，过滤掉 PR。

        如果 open issues 多于 max_count，只取前 max_count。
        """
        per_page = min(100, max(1, max_count))
        params: dict[str, Any] = {
            "state": "open",
            "sort": "created",
            "direction": "desc",
            "per_page": per_page,
        }
        if labels:
            params["labels"] = ",".join(labels)

        collected: list[GHIssue] = []
        page = 1
        while len(collected) < max_count:
            params["page"] = page
            raw = await self._get(f"/repos/{owner}/{repo}/issues", params=params)
            if not isinstance(raw, list) or not raw:
                break
            for item in raw:
                if item.get("pull_request") is not None:
                    continue  # GH 把 PR 也归在 issues 接口里
                collected.append(GHIssue.model_validate(item))
                if len(collected) >= max_count:
                    break
            if len(raw) < per_page:
                break
            page += 1

        return collected

    async def list_recent_merged_prs(
        self,
        owner: str,
        repo: str,
        *,
        count: int = 10,
    ) -> list[GHPullRequest]:
        """最近 N 个 merged PR（按 updated desc，取 merged_at != None）。"""
        per_page = min(100, max(count * 2, count))
        params = {
            "state": "closed",
            "sort": "updated",
            "direction": "desc",
            "per_page": per_page,
        }
        raw = await self._get(f"/repos/{owner}/{repo}/pulls", params=params)
        if not isinstance(raw, list):
            return []
        merged: list[GHPullRequest] = []
        for item in raw:
            pr = GHPullRequest.model_validate(item)
            if pr.merged_at is not None:
                merged.append(pr)
            if len(merged) >= count:
                break
        return merged

    async def get_languages(self, owner: str, repo: str) -> dict[str, int]:
        data = await self._get(f"/repos/{owner}/{repo}/languages")
        return data if isinstance(data, dict) else {}

    async def get_file_content(
        self,
        owner: str,
        repo: str,
        path: str,
        *,
        ref: str | None = None,
        max_bytes: int = 80_000,
    ) -> str | None:
        """读取仓库内某个文件的文本内容。

        - 用 raw 媒体类型直取（避免 base64 解码）
        - 404 / 403 / 文件过大（GitHub raw 端点 1MB 限制）→ None
        - max_bytes 截断（防止 README 太长撑爆 prompt）
        """
        params: dict[str, Any] = {}
        if ref:
            params["ref"] = ref
        try:
            resp = await self._client.get(
                f"/repos/{owner}/{repo}/contents/{path}",
                params=params,
                headers={"Accept": "application/vnd.github.raw+json"},
            )
        except httpx.RequestError:
            return None
        self.last_rate_limit = RateLimit.from_headers(resp.headers)
        if resp.status_code != 200:
            return None
        text = resp.text or ""
        if len(text) > max_bytes:
            text = text[:max_bytes] + "\n\n[... truncated ...]\n"
        return text

    async def get_pr_diff(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        max_bytes: int = 8_000,
    ) -> str | None:
        """获取 PR 的 unified diff 文本（截断后用于 Agent D 学习风格）。

        404 / 403 / 大于 max_bytes 时返回截断字符串；失败返回 None。
        """
        try:
            resp = await self._client.get(
                f"/repos/{owner}/{repo}/pulls/{number}",
                headers={"Accept": "application/vnd.github.diff"},
            )
        except httpx.RequestError:
            return None
        self.last_rate_limit = RateLimit.from_headers(resp.headers)
        if resp.status_code != 200:
            return None
        text = resp.text or ""
        if len(text) > max_bytes:
            text = text[:max_bytes] + "\n\n[... diff truncated ...]\n"
        return text

    async def get_rate_limit(self) -> RateLimit:
        data = await self._get("/rate_limit")
        core = data.get("resources", {}).get("core", data.get("rate", {}))
        return RateLimit.model_validate(core) if "limit" in core else RateLimit()

    # ----- 内部 -----

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        resp = await self._client.get(path, params=params)
        self.last_rate_limit = RateLimit.from_headers(resp.headers)

        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            raise RepoNotFoundError(f"GET {path} → 404", status_code=404)
        if resp.status_code in (401, 403):
            remaining = resp.headers.get("X-RateLimit-Remaining")
            if remaining == "0":
                retry_after = self._rate_limit_retry_after(resp)
                raise RateLimitedError(
                    f"GET {path} → rate limited",
                    retry_after_seconds=retry_after,
                )
            if resp.status_code == 401:
                raise GitHubError(f"GET {path} → 401 unauthorized", status_code=401)
            raise RepoForbiddenError(f"GET {path} → 403 forbidden", status_code=403)
        if resp.status_code == 429:
            raise RateLimitedError(
                f"GET {path} → 429",
                retry_after_seconds=self._rate_limit_retry_after(resp),
            )
        raise GitHubError(
            f"GET {path} → {resp.status_code}: {resp.text[:200]}",
            status_code=resp.status_code,
        )

    @staticmethod
    def _rate_limit_retry_after(resp: httpx.Response) -> int | None:
        retry_after_header = resp.headers.get("Retry-After")
        if retry_after_header:
            try:
                return int(retry_after_header)
            except (TypeError, ValueError):
                return None
        return None
