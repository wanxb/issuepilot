"""Agent B 卡死检测（2.3）单测。"""
from __future__ import annotations

import pytest

from app.agents.stuck_detector import StuckDetector


class TestStuckDetector:
    def test_default_window_size(self) -> None:
        d = StuckDetector()
        assert d.window_size == 12
        assert not d.is_stuck()

    def test_under_window_not_stuck(self) -> None:
        d = StuckDetector(window_size=5)
        for _ in range(4):
            d.observe("Read")
        assert not d.is_stuck()

    def test_full_window_all_reads_is_stuck(self) -> None:
        d = StuckDetector(window_size=4)
        for _ in range(4):
            d.observe("Read")
        assert d.is_stuck()

    def test_any_mutating_tool_breaks_stuck(self) -> None:
        d = StuckDetector(window_size=4)
        d.observe("Read")
        d.observe("Read")
        d.observe("Edit")          # 推进
        d.observe("Read")
        assert not d.is_stuck()

    def test_recent_writes_reset_via_sliding_window(self) -> None:
        """maxlen 行为：写入后再连读，但窗口里还有 Edit 时不算 stuck；
        Edit 滑出窗口后才可能 stuck。"""
        d = StuckDetector(window_size=3)
        d.observe("Edit")
        d.observe("Read")
        d.observe("Read")
        assert not d.is_stuck()   # 窗口 [Edit, Read, Read]
        d.observe("Read")
        assert d.is_stuck()       # 窗口 [Read, Read, Read]

    @pytest.mark.parametrize("tool", ["Bash", "Write", "Edit", "NotebookEdit", "MultiEdit"])
    def test_mutating_tools_recognized(self, tool: str) -> None:
        d = StuckDetector(window_size=3)
        d.observe("Read")
        d.observe(tool)
        d.observe("Read")
        assert not d.is_stuck()

    def test_unknown_tool_treated_as_not_stuck(self) -> None:
        """未知工具不算 stuck（保守，避免冤枉新增 tool）。"""
        d = StuckDetector(window_size=3)
        for _ in range(3):
            d.observe("SomeFutureTool")
        assert not d.is_stuck()

    def test_none_observation_ignored(self) -> None:
        d = StuckDetector(window_size=3)
        for _ in range(10):
            d.observe(None)
        assert not d.is_stuck()
        assert d.snapshot() == []

    def test_reset_clears_window(self) -> None:
        d = StuckDetector(window_size=3)
        d.observe("Read"); d.observe("Read"); d.observe("Read")
        assert d.is_stuck()
        d.reset()
        assert not d.is_stuck()
        assert d.snapshot() == []


class TestParsedLineToolName:
    """ParsedLine.tool_name 应正确填充 short tool name。"""

    def test_short_name_extracted(self) -> None:
        from app.agents.agent_b import AgentB

        line = (
            '{"type":"assistant","message":{"content":'
            '[{"type":"tool_use","id":"x","name":"Read","input":{}}]}}'
        )
        parsed = AgentB().parse_stream_line(line)
        assert parsed.tool_name == "Read"

    def test_mcp_prefix_stripped(self) -> None:
        from app.agents.agent_b import AgentB

        line = (
            '{"type":"assistant","message":{"content":'
            '[{"type":"tool_use","id":"x",'
            '"name":"mcp__some_server__SomeTool","input":{}}]}}'
        )
        parsed = AgentB().parse_stream_line(line)
        # MCP 短名 = 最后一段；report_* 被过滤，其他保留
        assert parsed.tool_name == "SomeTool"

    def test_report_tools_excluded(self) -> None:
        """report_completion / report_failure 不计入 stuck 窗口。"""
        from app.agents.agent_b import AgentB

        line = (
            '{"type":"assistant","message":{"content":'
            '[{"type":"tool_use","id":"x",'
            '"name":"mcp__issuepilot-agent-b-terminator__report_completion",'
            '"input":{}}]}}'
        )
        parsed = AgentB().parse_stream_line(line)
        assert parsed.tool_name is None
        assert parsed.report_kind == "completion"
