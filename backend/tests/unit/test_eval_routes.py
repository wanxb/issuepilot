"""3.1 dashboard 新增 endpoints + EvalSample model 单测。"""
from __future__ import annotations


def test_weekly_report_and_agent_quality_routes_registered() -> None:
    from app.main import app

    spec = app.openapi()
    paths = set(spec.get("paths", {}).keys())
    assert "/api/v1/dashboard/weekly-report" in paths
    assert "/api/v1/dashboard/agent-quality" in paths


def test_eval_sample_model_imports() -> None:
    """models/__init__.py 必须显式 import EvalSample 否则 alembic autogenerate
    + ORM 关系不可用。"""
    from app.models import EvalSample
    assert EvalSample.__tablename__ == "eval_samples"


def test_eval_sample_default_construction() -> None:
    """EvalSample(...) 构造时 UUID PK 应在 flush 时由 Python default 生成；
    本测仅校验所有字段都能赋值。"""
    import uuid
    from app.models import EvalSample

    sample = EvalSample(
        issue_id=uuid.uuid4(),
        sample_kind="positive_merged_clean",
        label="merged_clean",
        source="auto_pick_pr",
        notes="hello",
    )
    assert sample.sample_kind == "positive_merged_clean"
    assert sample.label == "merged_clean"
    assert sample.source == "auto_pick_pr"
    assert sample.notes == "hello"


def test_weekly_report_dashboard_router_prefix() -> None:
    from app.api.dashboard import router
    assert router.prefix == "/api/v1/dashboard"
    paths = {r.path for r in router.routes}  # type: ignore[attr-defined]
    assert "/api/v1/dashboard/weekly-report" in paths
    assert "/api/v1/dashboard/agent-quality" in paths
