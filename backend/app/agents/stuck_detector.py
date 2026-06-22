"""Agent B 卡死检测（2.3）。

设计原则：尽量不冤枉。Agent B 在 ANALYZE 阶段大量 Read 是合理的；
真卡死的特征是"持续 N 步只读不写、不调测、不跑命令"。

实现：在沙箱日志流处理循环里维护一个窗口（last N 个 tool_use 的简名），
当窗口被 READ_ONLY_TOOLS 填满且没有任何 mutating tool 时 → stuck。

阈值由 settings.agent_b_stuck_window 控制（默认 12，即连续 12 次只读
0 次写/测/Bash 就判定 stuck）。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

# 只读工具（视为"探索"，不推进任务）
READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "Read", "Glob", "Grep", "WebFetch", "WebSearch", "TodoWrite",
})

# 视为"推进"的工具（重置卡死窗口）
MUTATING_TOOLS: frozenset[str] = frozenset({
    "Write", "Edit", "NotebookEdit", "Bash", "MultiEdit",
})


@dataclass
class StuckDetector:
    window_size: int = 12
    _window: deque[str] = field(default_factory=lambda: deque(maxlen=12))

    def __post_init__(self) -> None:
        # 用配置的 window_size 重建 deque
        self._window = deque(maxlen=self.window_size)

    def observe(self, tool_name: str | None) -> None:
        """每次解析到 assistant 的 tool_use 调一次（None 表示纯 text 步）。"""
        if not tool_name:
            return
        self._window.append(tool_name)

    def is_stuck(self) -> bool:
        if len(self._window) < self.window_size:
            return False
        for t in self._window:
            if t in MUTATING_TOOLS:
                return False  # 窗口内有任何推进 → 不算卡死
            if t not in READ_ONLY_TOOLS:
                # 未知工具按非卡死处理（保守）
                return False
        return True

    def reset(self) -> None:
        self._window.clear()

    def snapshot(self) -> list[str]:
        return list(self._window)
