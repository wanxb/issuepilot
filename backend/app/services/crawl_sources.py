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


SUPPORTED_SOURCES = ("github_trending", "explicit_repos", "github_search")


class SourceConfigError(ValueError):
    """spec dict 不合法。"""


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def validate_spec(source: str, spec: dict[str, Any]) -> None:
    # 4.x: domains 是 trending / search 共用的领域白名单（必填）
    if source in ("github_trending", "github_search"):
        from app.services.crawl_domain import validate_domains

        try:
            validate_domains(spec.get("domains") or [])
        except ValueError as e:
            raise SourceConfigError(f"{source}.domains: {e}") from e

    if source == "github_trending":
        lang = spec.get("language")
        since = spec.get("since", "daily")
        if lang is not None and not isinstance(lang, str):
            raise SourceConfigError("github_trending.language must be string or null")
        if since not in ("daily", "weekly", "monthly"):
            raise SourceConfigError(
                f"github_trending.since must be daily/weekly/monthly, got {since!r}",
            )
    elif source == "github_search":
        # 4.x: 用 GitHub Search API 按 domain topic 直接搜
        sort = spec.get("sort", "stars")
        if sort not in ("stars", "forks", "updated", "best-match"):
            raise SourceConfigError(
                f"github_search.sort must be stars/forks/updated/best-match, got {sort!r}",
            )
        max_results = spec.get("max_results", 30)
        if not isinstance(max_results, int) or not 1 <= max_results <= 100:
            raise SourceConfigError(
                "github_search.max_results must be int in [1, 100]",
            )
        min_stars = spec.get("min_stars", 100)
        if not isinstance(min_stars, int) or min_stars < 0:
            raise SourceConfigError("github_search.min_stars must be int >= 0")
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
    """返回 ["owner/repo", ...]（仅名字；domain 过滤在 CrawlerService 后置）。"""
    if source == "github_trending":
        return await _fetch_trending(
            language=spec.get("language"),
            since=spec.get("since", "daily"),
        )
    if source == "github_search":
        return await _fetch_search(spec)
    if source == "explicit_repos":
        return list(spec.get("repos") or [])
    raise SourceConfigError(f"unsupported source: {source!r}")


async def _fetch_search(spec: dict[str, Any]) -> list[str]:
    """4.x: 用 GitHub Search API + crawl_domain 主动搜。

    GitHub Search API 不支持 qualifier 之间的 OR（topic:X OR topic:Y 会
    报 "Logical operators only apply to text"）。所以每个 domain 的每个
    flagship topic 都单独 query，结果合并 + 去重。
    """
    import datetime as _dt
    from app.github.client import GitHubClient
    from app.services.crawl_domain import _FLAGSHIP_TOPICS, DOMAIN_REGISTRY

    domains = spec.get("domains") or []
    if "all" in domains:
        domains = sorted(DOMAIN_REGISTRY.keys())
    min_stars = int(spec.get("min_stars", 100))
    pushed_within_days = int(spec.get("pushed_within_days", 180))
    pushed_since = (
        _dt.date.today() - _dt.timedelta(days=pushed_within_days)
    ).isoformat()
    qualifiers = (
        f"stars:>={min_stars} archived:false is:public "
        f"pushed:>{pushed_since}"
    )
    sort = spec.get("sort", "stars")
    max_results = int(spec.get("max_results", 30))

    # 每 domain 取 top-K topic 单查
    per_domain_topics = 3
    queries: list[str] = []
    for dname in domains:
        spec_d = DOMAIN_REGISTRY.get(dname)
        if spec_d is None:
            continue
        topics = _FLAGSHIP_TOPICS.get(dname, sorted(spec_d.topics))[:per_domain_topics]
        for t in topics:
            queries.append(f"topic:{t} {qualifiers}")

    # 每 query 拿 per_query 个结果，最后整体截到 max_results
    per_query = max(5, max_results // max(len(queries), 1))
    collected: list[str] = []
    seen: set[str] = set()

    async with GitHubClient.from_settings() as gh:
        for q in queries:
            try:
                items = await gh.search_repositories(
                    q=q, sort=sort, order="desc",
                    per_page=min(50, per_query),
                    max_results=per_query,
                )
            except Exception as e:
                log.warning("crawl_sources.search_query_failed",
                            q=q[:120], error=str(e)[:200])
                continue
            for it in items:
                full = it.get("full_name")
                if full and full not in seen:
                    seen.add(full)
                    collected.append(full)
                    if len(collected) >= max_results:
                        break
            if len(collected) >= max_results:
                break

    log.info(
        "crawl_sources.search_fetched",
        count=len(collected), sort=sort, domains=domains,
        query_count=len(queries),
    )
    return collected[:max_results]


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
