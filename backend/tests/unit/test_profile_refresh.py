"""RepoProfile 智能刷新（3.2）单测。

只测纯逻辑路径（DEFAULT_LOOKBACK_DAYS / DEFAULT_THRESHOLD 常量 + 函数签名）；
session × DB 行为依赖真实 Postgres，留给 e2e 验证。
"""
from __future__ import annotations

from app.services.profile_refresh import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_THRESHOLD,
    count_style_rejections_for_repo,
    maybe_force_refresh,
)


def test_defaults_match_3_2_milestone() -> None:
    """ROADMAP 默认值变更需同步本测。"""
    assert DEFAULT_LOOKBACK_DAYS == 30
    assert DEFAULT_THRESHOLD == 3


def test_callable_signatures_exist() -> None:
    """模块顶层 export 都得能 import（防 ImportError 静默回归）。"""
    assert callable(count_style_rejections_for_repo)
    assert callable(maybe_force_refresh)


def test_classify_worker_imports_profile_refresh() -> None:
    """classify_worker 必须能 import maybe_force_refresh（线上路径）。"""
    from app.workers.classify_worker import _classify_async  # noqa: F401
    # 只要不抛 ImportError 即可
    from app.services.profile_refresh import maybe_force_refresh  # noqa: F401
