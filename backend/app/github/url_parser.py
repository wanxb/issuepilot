"""GitHub URL 解析。

支持的输入形态：
    https://github.com/owner/repo
    https://github.com/owner/repo/
    https://github.com/owner/repo/issues/42
    https://github.com/owner/repo/pull/42        # 视为 invalid（手动入口只评估 issue）
    http://github.com/...                         # 协议兼容
    github.com/owner/repo                         # 缺协议补全

返回 ParsedURL，调用方根据 .mode 分发：
    - "repo":  拉该 repo 的 open issues
    - "issue": 单 Issue 直接入评估队列
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

URLMode = Literal["repo", "issue"]

# owner / repo 的字符集（GitHub 限制：字母数字、-、_、.）
_NAME_RE = r"[A-Za-z0-9._-]+"
_REPO_PATH_RE = re.compile(rf"^/(?P<owner>{_NAME_RE})/(?P<repo>{_NAME_RE})/?$")
_ISSUE_PATH_RE = re.compile(
    rf"^/(?P<owner>{_NAME_RE})/(?P<repo>{_NAME_RE})/issues/(?P<number>\d+)/?$"
)
_PR_PATH_RE = re.compile(
    rf"^/(?P<owner>{_NAME_RE})/(?P<repo>{_NAME_RE})/pull/(?P<number>\d+)/?$"
)


@dataclass(frozen=True, slots=True)
class ParsedURL:
    mode: URLMode
    owner: str
    repo: str
    issue_number: int | None = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


class InvalidGitHubURLError(ValueError):
    """无法识别的 GitHub URL。"""


def parse_github_url(url: str) -> ParsedURL:
    """解析 GitHub URL 为 ParsedURL；无法识别时抛 InvalidGitHubURLError。"""
    raw = (url or "").strip()
    if not raw:
        raise InvalidGitHubURLError("empty url")

    # 缺协议时补全
    if "://" not in raw:
        raw = "https://" + raw.lstrip("/")

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host not in ("github.com", "www.github.com"):
        raise InvalidGitHubURLError(f"not a github.com URL: host={host!r}")

    path = parsed.path or "/"

    if m := _ISSUE_PATH_RE.match(path):
        return ParsedURL(
            mode="issue",
            owner=m.group("owner"),
            repo=_strip_dot_git(m.group("repo")),
            issue_number=int(m.group("number")),
        )

    if _PR_PATH_RE.match(path):
        raise InvalidGitHubURLError(
            "PR URL not supported as a manual input — paste the related issue URL instead",
        )

    if m := _REPO_PATH_RE.match(path):
        return ParsedURL(
            mode="repo",
            owner=m.group("owner"),
            repo=_strip_dot_git(m.group("repo")),
        )

    raise InvalidGitHubURLError(f"unrecognized github path: {path!r}")


def _strip_dot_git(name: str) -> str:
    """`repo.git` → `repo`（克隆 URL 偶尔带 .git 后缀）。"""
    return name[:-4] if name.endswith(".git") else name
