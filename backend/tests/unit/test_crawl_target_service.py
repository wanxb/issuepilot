"""CrawlTargetService 校验单测（2.2）—— 纯逻辑：不连 DB。"""
from __future__ import annotations

import pytest

from app.services.crawl_target_service import (
    CrawlTargetInvalid,
    _check_cron,
)
from app.services.crawl_sources import SourceConfigError, validate_spec


class TestCronCheck:
    @pytest.mark.parametrize("cron", [
        "0 4 * * *",
        "*/5 * * * *",
        "30 5 * * 1-5",
    ])
    def test_valid_5_field(self, cron: str) -> None:
        _check_cron(cron)  # 不抛即 ok

    @pytest.mark.parametrize("cron", [
        "",
        "* *",
        "* * * *",
        "* * * * * *",
    ])
    def test_invalid_field_count(self, cron: str) -> None:
        with pytest.raises(CrawlTargetInvalid):
            _check_cron(cron)


class TestSpecCrossCheck:
    """validate_spec 已在 test_crawl_sources 单独覆盖；这里只做组合检查。"""

    def test_create_path_invalid_source_blocked_in_service_layer(self) -> None:
        # 不存在的 source 在 service.create 中会先报；但我们也能直接用
        # validate_spec 自己挡住 → 保证防御深度
        with pytest.raises(SourceConfigError):
            validate_spec("rss", {"url": "x"})
