"""crawl_sources（2.2）—— spec 校验 + trending HTML 解析。"""
from __future__ import annotations

import pytest

from app.services.crawl_sources import (
    SourceConfigError,
    SUPPORTED_SOURCES,
    _ARTICLE_RE,
    _HREF_RE,
    validate_spec,
)


class TestValidateSpec:
    def test_supported_sources(self) -> None:
        assert "github_trending" in SUPPORTED_SOURCES
        assert "explicit_repos" in SUPPORTED_SOURCES

    @pytest.mark.parametrize("since", ["daily", "weekly", "monthly"])
    def test_trending_valid(self, since: str) -> None:
        validate_spec(
            "github_trending",
            {"language": "python", "since": since, "domains": ["ai_agent"]},
        )

    def test_trending_no_language_ok(self) -> None:
        # language 可省（不限语言）
        validate_spec("github_trending",
                      {"since": "daily", "domains": ["llm"]})

    def test_trending_invalid_since(self) -> None:
        with pytest.raises(SourceConfigError):
            validate_spec(
                "github_trending",
                {"language": "python", "since": "yearly", "domains": ["llm"]},
            )

    def test_trending_non_string_language(self) -> None:
        with pytest.raises(SourceConfigError):
            validate_spec(
                "github_trending",
                {"language": 123, "since": "daily", "domains": ["llm"]},
            )

    # 4.x: domain 白名单是必填
    def test_trending_missing_domains_rejected(self) -> None:
        with pytest.raises(SourceConfigError):
            validate_spec("github_trending", {"language": "python", "since": "daily"})

    def test_trending_unknown_domain_rejected(self) -> None:
        with pytest.raises(SourceConfigError):
            validate_spec(
                "github_trending",
                {"language": "python", "since": "daily", "domains": ["fintech"]},
            )

    def test_github_search_valid(self) -> None:
        validate_spec("github_search", {
            "domains": ["ai_agent", "llm"], "sort": "stars",
            "max_results": 30, "min_stars": 500,
        })

    def test_github_search_invalid_sort(self) -> None:
        with pytest.raises(SourceConfigError):
            validate_spec("github_search", {
                "domains": ["llm"], "sort": "trending",
            })

    def test_github_search_invalid_max_results(self) -> None:
        with pytest.raises(SourceConfigError):
            validate_spec("github_search", {
                "domains": ["llm"], "max_results": 9999,
            })

    def test_explicit_repos_valid(self) -> None:
        validate_spec("explicit_repos", {"repos": ["psf/requests", "pallets/flask"]})

    def test_explicit_repos_empty(self) -> None:
        with pytest.raises(SourceConfigError):
            validate_spec("explicit_repos", {"repos": []})

    def test_explicit_repos_bad_full_name(self) -> None:
        with pytest.raises(SourceConfigError):
            validate_spec("explicit_repos", {"repos": ["just-name"]})

    def test_explicit_repos_not_list(self) -> None:
        with pytest.raises(SourceConfigError):
            validate_spec("explicit_repos", {"repos": "psf/requests"})

    def test_unsupported_source(self) -> None:
        with pytest.raises(SourceConfigError):
            validate_spec("rss", {})


class TestTrendingRegex:
    """GitHub Trending HTML 结构变动是高风险，单测两个核心正则。"""

    SAMPLE = """
    <article class="Box-row">
      <h2 class="h3 lh-condensed">
        <a href="/psf/requests">
          psf / requests
        </a>
      </h2>
      <p>HTTP for Humans.</p>
    </article>
    <article class="Box-row">
      <h2 class="h3 lh-condensed">
        <a href="/pallets/flask">pallets / flask</a>
      </h2>
    </article>
    <article class="Box-row some-other-class">
      <h2 class="h3 lh-condensed">
        <a href="/owner-c/repo-c?query=1">owner-c / repo-c</a>
      </h2>
    </article>
    """

    def test_article_re_finds_all(self) -> None:
        assert len(_ARTICLE_RE.findall(self.SAMPLE)) == 3

    def test_href_re_extracts_repo(self) -> None:
        m = _HREF_RE.search('<h2><a href="/psf/requests">x</a></h2>')
        assert m is not None
        assert m.group(1) == "psf/requests"

    def test_href_re_skips_query_string(self) -> None:
        # 实际 trending 卡片可能带 ?since=daily 之类，被正则的 [^#?] 截断
        m = _HREF_RE.search('<h2><a href="/owner/repo?query=1">x</a></h2>')
        assert m is not None
        assert m.group(1) == "owner/repo"
