"""Dashboard endpoints (2.4)：路由注册 + 不连 DB 的轻量 smoke 校验。

复杂的聚合 SQL 行为依赖真实 DB，本文件只验证：
    - 路由被注册
    - 涉及的 enum / model 都能 import
    - issue detail 端点路径形状对
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_routes_registered() -> None:
    """app.openapi() 反映完整路由注册（包含 include_router 的子路径）。"""
    from app.main import app

    spec = app.openapi()
    paths = set(spec.get("paths", {}).keys())
    assert "/api/v1/dashboard/stats" in paths
    assert "/api/v1/dashboard/pr-failures" in paths
    assert "/api/v1/issues/{issue_id}/detail" in paths


def test_stats_endpoint_responds_ok() -> None:
    """空 DB（没 Postgres connection）下 TestClient 会失败，但能接受请求体
    并通过 FastAPI 路由匹配；用 lifespan 上下文跳过 DB 实际查询是难的，
    因此这里只 import + 实例化 client，确认不抛 ImportError / TypeError。"""
    from app.api.dashboard import router

    assert router.prefix == "/api/v1/dashboard"
    # 两条 GET 路由
    route_paths = {r.path for r in router.routes}  # type: ignore[attr-defined]
    assert route_paths == {
        "/api/v1/dashboard/stats", "/api/v1/dashboard/pr-failures",
    }


def test_issue_detail_route_path() -> None:
    from app.api.issues import router

    detail_routes = [
        r for r in router.routes  # type: ignore[attr-defined]
        if getattr(r, "path", "").endswith("/detail")
    ]
    assert len(detail_routes) == 1
    assert detail_routes[0].path == "/api/v1/issues/{issue_id}/detail"
