"""定时抓取的 source 实现（2.2）。

每个 source 给 spec dict 加少量 schema 校验 + 调用对应的拉 repo URL 列表函数。
GitHub Trending 没有官方 API，scrape HTML（格式稳定，正则够用）。
"""
from __future__ import annotations

import re
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)


SUPPORTED_SOURCES = ("github_trending", "explicit_repos")


class SourceConfigError(ValueError):
    """spec dict 不合法。"""


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def validate_spec(source: str, spec: dict[str, Any]) -> None:
    if source == "github_trending":
        lang = spec.get("language")
        since = spec.get("since", "daily")
        if lang is not None and not isinstance(lang, str):
            raise SourceConfigError("github_trending.language must be string or null")
        if since not in ("daily", "weekly", "monthly"):
            raise SourceConfigError(
                f"github_trending.since must be daily/weekly/monthly, got {since!r}",
            )
    elif source == "explicit_repos":
        repos = spec.get("repos")
        if not isinstance(repos, list) or not all(isinstance(r, str) for r in repos):
            raise SourceConfigError("explicit_repos.repos must be list[str]")
        if not repos:
            raise SourceConfigError("explicit_repos.repos cannot be empty")
        for r in repos:
            if "/" not in r:
                raise SourceConfigError(f"invalid repo full name: {r!r}")
    else:
        raise SourceConfigError(
            f"unsupported source: {source!r}; "
            f"supported: {', '.join(SUPPORTED_SOURCES)}",
        )


# ---------------------------------------------------------------------------
# 拉 repo URL 列表
# ---------------------------------------------------------------------------


async def fetch_repos(source: str, spec: dict[str, Any]) -> list[str]:
    """返回 ["owner/repo", ...]。"""
    if source == "github_trending":
        return await _fetch_trending(
            language=spec.get("language"),
            since=spec.get("since", "daily"),
        )
    if source == "explicit_repos":
        return list(spec.get("repos") or [])
    raise SourceConfigError(f"unsupported source: {source!r}")


# ---------------------------------------------------------------------------
# GitHub Trending HTML scraper
# ---------------------------------------------------------------------------


_TRENDING_URL = "https://github.com/trending"
_ARTICLE_RE = re.compile(
    r'<article class="Box-row[^"]*">(.*?)</article>',
    re.DOTALL,
)
# 匹配 href="/owner/repo"（trending repo 卡片里的主链接）；
# 容忍 href 末尾可能带 ?query / #frag，但只捕获 owner/repo 部分。
_HREF_RE = re.compile(
    r'<h2[^>]*>.*?<a\s+href="/([^/"#?]+/[^/"#?]+)[^"]*"', re.DOTALL,
)


async def _fetch_trending(
    *, language: str | None, since: str = "daily",
) -> list[str]:
    """scrape GitHub Trending 页面拿 repo 列表。

    URL 形如 https://github.com/trending/python?since=daily（language 可空
    → 不限语言）。返回去重后的 owner/repo 列表，顺序保留 top → bottom。
    """
    url = _TRENDING_URL
    if language:
        url = f"{_TRENDING_URL}/{language}"
    params = {"since": since}

    async with httpx.AsyncClient(timeout=15.0) as cli:
        resp = await cli.get(
            url, params=params,
            headers={
                # 装得像浏览器一些，避免被 GitHub 防爬命中
                "User-Agent": (
                    "Mozilla/5.0 (compatible; IssuePilot/0.1; "
                    "+https://github.com/anthropics/issuepilot)"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        resp.raise_for_status()
        html = resp.text

    repos: list[str] = []
    seen: set[str] = set()
    for art in _ARTICLE_RE.finditer(html):
        m = _HREF_RE.search(art.group(1))
        if not m:
            continue
        repo = m.group(1).strip()
        # 过滤明显不是 repo 的（trending 偶尔混入 features/links）
        if repo.count("/") != 1 or repo in seen:
            continue
        seen.add(repo)
        repos.append(repo)

    log.info("crawl_sources.trending_fetched",
             count=len(repos), language=language, since=since)
    return repos
