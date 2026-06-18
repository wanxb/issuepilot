"""PRService —— 在 GitHub 上创建 PR（里程碑 1.5d）。

只暴露 create_pr() 主入口；调用方传齐元数据（base/head owner+repo+branch、
标题、正文）。无 dev token 时直接拒绝（同 repo PR 在 1.5d 不支持，
避免误向自己创建 PR）。

调用：
    svc = PRService.from_settings()
    pr = await svc.create_pr(
        base_repo="owner/repo", base_branch="main",
        head_repo="dev/repo",   head_branch="issuepilot/issue-42-fix",
        title="fix: ...", body="## Summary ...",
    )

异常：
    NoDevTokenError      —— 未配置 GITHUB_DEV_TOKEN
    BranchNotPushedError —— head 分支 GitHub 上不存在（沙箱 push 失败）
    DuplicatePRError     —— 同一 head→base 已有 open PR（422 + 特定 message）
    PRCreationError     —— 其他 GitHub API 失败
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import structlog

from app.core.config import get_settings

GITHUB_API_BASE = "https://api.github.com"
ACCEPT = "application/vnd.github+json"
API_VERSION = "2022-11-28"

log = structlog.get_logger(__name__)


class PRServiceError(Exception):
    pass


class NoDevTokenError(PRServiceError):
    pass


class BranchNotPushedError(PRServiceError):
    pass


class DuplicatePRError(PRServiceError):
    """同一 head→base 已经有 open PR（GitHub 422）。"""

    def __init__(self, message: str, *, existing_url: str | None = None) -> None:
        super().__init__(message)
        self.existing_url = existing_url


class PRCreationError(PRServiceError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(slots=True)
class CreatedPR:
    number: int
    url: str
    title: str
    body: str
    submitted_at: datetime
    base_branch: str


class PRService:
    def __init__(self, *, dev_token: str | None) -> None:
        self._dev_token = dev_token

    @classmethod
    def from_settings(cls) -> "PRService":
        s = get_settings()
        return cls(
            dev_token=s.github_dev_token.get_secret_value() if s.github_dev_token else None,
        )

    # ---- 帮助方法 ----

    async def get_default_branch(self, owner: str, repo: str) -> str:
        if not self._dev_token:
            return "main"
        async with self._client() as cli:
            resp = await cli.get(f"/repos/{owner}/{repo}")
            if resp.status_code != 200:
                log.warning(
                    "pr_service.get_repo_failed",
                    owner=owner, repo=repo, code=resp.status_code,
                )
                return "main"
            return str(resp.json().get("default_branch") or "main")

    async def branch_exists(self, owner: str, repo: str, branch: str) -> bool:
        if not self._dev_token:
            return False
        async with self._client() as cli:
            resp = await cli.get(f"/repos/{owner}/{repo}/branches/{branch}")
            return resp.status_code == 200

    # ---- 主流程 ----

    async def create_pr(
        self,
        *,
        base_repo: str,        # "owner/repo"
        base_branch: str,
        head_repo: str,        # "dev_owner/repo"（fork）
        head_branch: str,
        title: str,
        body: str,
    ) -> CreatedPR:
        if not self._dev_token:
            raise NoDevTokenError("GITHUB_DEV_TOKEN not configured; cannot push or create PR")

        base_owner, base_name = base_repo.split("/", 1)
        head_owner, _ = head_repo.split("/", 1)

        # 推前置检查：head 分支必须真的在 GitHub 上
        if not await self.branch_exists(head_owner, base_name, head_branch):
            raise BranchNotPushedError(
                f"head branch {head_owner}/{base_name}:{head_branch} not found on GitHub"
            )

        payload = {
            "title": title[:240],
            "body": body,
            "head": f"{head_owner}:{head_branch}",
            "base": base_branch,
            "maintainer_can_modify": True,
        }

        async with self._client() as cli:
            resp = await cli.post(f"/repos/{base_owner}/{base_name}/pulls", json=payload)

        if resp.status_code == 201:
            data = resp.json()
            return CreatedPR(
                number=int(data["number"]),
                url=str(data["html_url"]),
                title=str(data.get("title") or title),
                body=str(data.get("body") or body),
                submitted_at=_parse_dt(data.get("created_at")),
                base_branch=base_branch,
            )

        # 422：常见原因 = 同一 head 已有 open PR
        if resp.status_code == 422:
            payload_json = _safe_json(resp)
            top_message = str(payload_json.get("message") or resp.text[:200])
            errors = payload_json.get("errors") or []
            sub_messages = " | ".join(
                str(e.get("message", "")) for e in errors if isinstance(e, dict)
            )
            combined = f"{top_message} | {sub_messages}".lower()
            existing_url = _extract_existing_pr_url(payload_json)
            if existing_url or "pull request already exists" in combined:
                raise DuplicatePRError(
                    f"{top_message} | {sub_messages}".strip(" |"),
                    existing_url=existing_url,
                )
            raise PRCreationError(
                f"create_pr 422: {top_message} | {sub_messages}",
                status_code=422,
            )

        raise PRCreationError(
            f"create_pr {resp.status_code}: {resp.text[:300]}",
            status_code=resp.status_code,
        )

    # ---- 内部 ----

    def _client(self) -> httpx.AsyncClient:
        headers = {
            "Authorization": f"Bearer {self._dev_token}",
            "Accept": ACCEPT,
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "IssuePilot/0.1",
        }
        return httpx.AsyncClient(
            base_url=GITHUB_API_BASE,
            headers=headers,
            timeout=30.0,
        )


def _safe_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _extract_existing_pr_url(payload: dict) -> str | None:
    """422 响应里常带 errors=[{message: "A pull request already exists for ..."}]，
    但 URL 不在 payload 里——返回 None；caller 可日后通过 list_pulls 兜底查找。
    """
    return None


def _parse_dt(s: object) -> datetime:
    if isinstance(s, str):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)
