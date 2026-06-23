"""Phase 3.3 周报导出（Markdown）单测。"""
from __future__ import annotations


def _sample_payload() -> dict:
    return {
        "since": "2026-06-16T00:00:00+00:00",
        "days": 7,
        "totals": {"calls": 100, "cost_usd": 0.12345},
        "top_failure_modes": [
            {"category": "style_mismatch", "agent_b_attribution": "yes", "count": 3},
            {"category": "wrong_root_cause", "agent_b_attribution": "unclear", "count": 2},
        ],
        "cost_by_agent": [
            {"agent_kind": "agent_a", "calls": 60, "input_tokens": 12000,
             "output_tokens": 8000, "cost_usd": 0.10, "fallback_calls": 10},
            {"agent_kind": "agent_c", "calls": 5, "input_tokens": 2000,
             "output_tokens": 500, "cost_usd": 0.02, "fallback_calls": 0},
        ],
        "attribution_ratio": {
            "yes": {"count": 3, "ratio": 0.6},
            "no": {"count": 2, "ratio": 0.4},
        },
        "issue_funnel": {
            "DISCOVERED": 50, "PENDING_DECISION": 5, "PR_MERGED": 1,
            "IGNORED": 0, "ARCHIVED": 2,
        },
        "pr_outcomes": {"MERGED_CLEAN": 1, "CLOSED_BY_MAINTAINER": 0},
    }


def test_renders_h1_with_days() -> None:
    from app.services.report_export import render_weekly_report_markdown

    md = render_weekly_report_markdown(_sample_payload())
    assert md.startswith("# IssuePilot 周报（7 天）")


def test_totals_section() -> None:
    from app.services.report_export import render_weekly_report_markdown

    md = render_weekly_report_markdown(_sample_payload())
    assert "**100**" in md
    assert "**$0.1235**" in md or "**$0.1234**" in md


def test_top_failure_modes_rows() -> None:
    from app.services.report_export import render_weekly_report_markdown

    md = render_weekly_report_markdown(_sample_payload())
    assert "`style_mismatch`" in md
    assert "`wrong_root_cause`" in md


def test_cost_table_rows() -> None:
    from app.services.report_export import render_weekly_report_markdown

    md = render_weekly_report_markdown(_sample_payload())
    assert "`agent_a`" in md
    assert "12,000 / 8,000" in md   # token 数千分位
    assert "$0.1000" in md


def test_funnel_zero_filtered_out() -> None:
    from app.services.report_export import render_weekly_report_markdown

    md = render_weekly_report_markdown(_sample_payload())
    # IGNORED 数量 0 应被过滤
    assert "`IGNORED`" not in md
    # 有数据的应保留
    assert "`DISCOVERED`" in md
    assert "`PR_MERGED`" in md


def test_empty_payload_handled() -> None:
    from app.services.report_export import render_weekly_report_markdown

    md = render_weekly_report_markdown({
        "days": 7, "since": "x", "totals": {"calls": 0, "cost_usd": 0},
        "top_failure_modes": [], "cost_by_agent": [],
        "attribution_ratio": {}, "issue_funnel": {}, "pr_outcomes": {},
    })
    assert "无 PR 退回信号" in md
    assert "无 LLM 调用记录" in md
    assert "无 rejection_reasons 数据" in md


def test_markdown_route_registered() -> None:
    from app.main import app

    paths = set(app.openapi().get("paths", {}).keys())
    assert "/api/v1/dashboard/weekly-report.md" in paths
