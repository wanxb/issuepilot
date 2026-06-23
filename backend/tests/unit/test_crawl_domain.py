"""crawl_domain（4.x）单测：3 个领域 match 边界 + 搜索 q 构造。"""
from __future__ import annotations

import pytest

from app.services.crawl_domain import (
    DOMAIN_REGISTRY,
    KNOWN_DOMAINS,
    build_github_search_query,
    match_repo,
    validate_domains,
)


class TestRegistry:
    def test_three_domains_only(self) -> None:
        """变更 domain 必须同步本测；防止意外引入低质量 domain。"""
        assert KNOWN_DOMAINS == frozenset({"ai_agent", "llm", "robotics"})

    def test_topics_non_empty(self) -> None:
        for spec in DOMAIN_REGISTRY.values():
            assert spec.topics, f"{spec.name} topics empty"


class TestMatchTopicHit:
    def test_ai_agent_topic(self) -> None:
        m = match_repo(
            name="x", description="",
            topics=["ai-agent", "python"],
            accepted_domains=["ai_agent", "llm", "robotics"],
        )
        assert m.matched_domain == "ai_agent"
        assert "topic:" in m.signal

    def test_llm_topic(self) -> None:
        m = match_repo(
            name="x", description="",
            topics=["llm", "transformer"],
            accepted_domains=["ai_agent", "llm", "robotics"],
        )
        assert m.matched_domain == "llm"

    def test_robotics_topic(self) -> None:
        m = match_repo(
            name="x", description="",
            topics=["ros2", "robotics"],
            accepted_domains=["ai_agent", "llm", "robotics"],
        )
        assert m.matched_domain == "robotics"

    def test_only_accepts_in_scope(self) -> None:
        """topics 命中但 caller 不接受该 domain → miss。"""
        m = match_repo(
            name="x", description="", topics=["robotics"],
            accepted_domains=["ai_agent", "llm"],  # 不含 robotics
        )
        assert m.matched_domain is None


class TestMatchKeywordHit:
    @pytest.mark.parametrize("desc,expected", [
        ("RAG framework for chatbots", "llm"),
        ("multi-agent system for task automation", "ai_agent"),
        ("ROS2 navigation stack", "robotics"),
        ("Embodied AI research codebase", "robotics"),
        ("Vector database with embedding search", "llm"),
        ("LangChain wrapper for Claude", "ai_agent"),
        ("Train a humanoid robot with SLAM", "robotics"),
    ])
    def test_description_keywords(self, desc: str, expected: str) -> None:
        m = match_repo(
            name="x", description=desc, topics=[],
            accepted_domains=["ai_agent", "llm", "robotics"],
        )
        assert m.matched_domain == expected, (
            f"desc={desc!r} matched {m.matched_domain} (signal={m.signal}), "
            f"expected {expected}"
        )

    def test_no_signal(self) -> None:
        m = match_repo(
            name="cool-tool", description="A nice CLI for managing dotfiles.",
            topics=["cli", "dotfiles"],
            accepted_domains=["ai_agent", "llm", "robotics"],
        )
        assert m.matched_domain is None
        assert m.signal == "none"


class TestValidateDomains:
    def test_known(self) -> None:
        assert validate_domains(["ai_agent", "llm"]) == ["ai_agent", "llm"]

    def test_dedup(self) -> None:
        assert validate_domains(["llm", "llm"]) == ["llm"]

    def test_normalize_case(self) -> None:
        assert validate_domains(["LLM", "Robotics"]) == ["llm", "robotics"]

    def test_unknown_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown domain"):
            validate_domains(["fintech"])

    def test_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_domains([])

    def test_all_keyword_accepted(self) -> None:
        """'all' 是显式接受全部已知 domain 的语法糖。"""
        assert validate_domains(["all"]) == ["all"]

    def test_non_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be string"):
            validate_domains([123])  # type: ignore[list-item]


class TestBuildSearchQuery:
    def test_single_domain(self) -> None:
        q = build_github_search_query(domains=["robotics"])
        # query 含至少一个 robotics domain 的 topic
        from app.services.crawl_domain import DOMAIN_REGISTRY
        robotics_topics = DOMAIN_REGISTRY["robotics"].topics
        assert any(f"topic:{t}" in q for t in robotics_topics)

    def test_multi_domain(self) -> None:
        q = build_github_search_query(domains=["ai_agent", "llm"])
        assert "topic:" in q
        assert " OR " in q

    def test_extra_qualifiers_appended(self) -> None:
        q = build_github_search_query(
            domains=["llm"], extra_qualifiers="stars:>500 archived:false",
        )
        assert "stars:>500" in q
        assert "archived:false" in q

    def test_query_length_capped(self) -> None:
        q = build_github_search_query(domains=["ai_agent", "llm", "robotics"])
        assert len(q) < 260   # GitHub Search API 极限


class TestPostFilterContract:
    """Crawler 集成时承诺：spec.domains 非空 → fetch 完后必须过滤；空 → 不过滤。"""

    def test_match_returns_none_signals_off_topic(self) -> None:
        m = match_repo(name="x", description="", topics=[],
                       accepted_domains=["llm"])
        assert m.matched_domain is None
