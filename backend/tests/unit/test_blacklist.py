"""Blacklist 服务（3.3a）单测。"""
from __future__ import annotations

import pytest

from app.services.blacklist_service import _match


class TestPatternMatch:
    @pytest.mark.parametrize("pattern,value,expected", [
        ("owner/repo", "owner/repo", True),
        ("owner/repo", "owner/other", False),
        ("owner/*", "owner/foo", True),
        ("owner/*", "owner/bar", True),
        ("owner/*", "other/foo", False),
        ("*/repo", "any/repo", True),
        ("*/repo", "any/other", False),
        ("owner/repo#42", "owner/repo#42", True),
        ("owner/repo#42", "owner/repo#43", False),
        ("owner/repo#*", "owner/repo#42", True),
        ("owner/repo#*", "owner/repo#999", True),
    ])
    def test_match(self, pattern: str, value: str, expected: bool) -> None:
        assert _match(pattern, value) is expected


def test_model_import_and_registration() -> None:
    """models/__init__.py 必须 export Blacklist；否则 alembic 看不见。"""
    from app.models import Blacklist
    assert Blacklist.__tablename__ == "blacklist"


def test_blacklist_admin_routes_registered() -> None:
    from app.main import app

    spec = app.openapi()
    paths = set(spec.get("paths", {}).keys())
    assert "/api/v1/admin/blacklist" in paths
    assert "/api/v1/admin/blacklist/{blacklist_id}" in paths


def test_blacklist_endpoint_validation() -> None:
    """API 层的请求 schema 必须拒绝非法 entity_type。"""
    from app.api.admin import BlacklistAddRequest

    # 合法
    ok = BlacklistAddRequest.model_validate({
        "entity_type": "repo", "pattern": "owner/*",
    })
    assert ok.entity_type == "repo"
    assert ok.enabled is True   # default

    # 非法 entity_type
    with pytest.raises(Exception):
        BlacklistAddRequest.model_validate({
            "entity_type": "user", "pattern": "x",
        })


def test_crawler_blacklisted_error_class() -> None:
    """CrawlerService 抛 Blacklisted 时 API 层能 catch（同 module 导出）。"""
    from app.services.crawler_service import Blacklisted, CrawlerError
    err = Blacklisted("test", pattern="owner/*")
    assert isinstance(err, CrawlerError)
    assert err.code == "BLACKLISTED"
    assert err.pattern == "owner/*"
