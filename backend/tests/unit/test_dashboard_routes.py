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


def test_dashboard_routes_contain_2_4_and_3_1_endpoints() -> None:
    """dashboard 路由必须覆盖：
        2.4：stats / pr-failures
        3.1：weekly-report / agent-quality
    """
    from app.api.dashboard import router

    assert router.prefix == "/api/v1/dashboard"
    route_paths = {r.path for r in router.routes}  # type: ignore[attr-defined]
    expected = {
        "/api/v1/dashboard/stats",
        "/api/v1/dashboard/pr-failures",
        "/api/v1/dashboard/weekly-report",
        "/api/v1/dashboard/agent-quality",
    }
    assert expected <= route_paths


def test_issue_detail_route_path() -> None:
    from app.api.issues import router

    detail_routes = [
        r for r in router.routes  # type: ignore[attr-defined]
        if getattr(r, "path", "").endswith("/detail")
    ]
    assert len(detail_routes) == 1
    assert detail_routes[0].path == "/api/v1/issues/{issue_id}/detail"
