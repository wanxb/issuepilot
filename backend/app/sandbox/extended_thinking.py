"""Agent B Extended Thinking 启用判定（3.2）。

策略：
    1. evaluation.difficulty 在 settings.agent_b_extended_thinking_difficulties
       命中（默认 "hard"）→ 启用
    2. dev_task.attempt_number >= settings.agent_b_extended_thinking_min_attempt
       （默认 2，即 retry 时启用）

任一条件成立即启用，写到沙箱 env 由 entrypoint.sh 接管 prompt 引导。
"""
from __future__ import annotations


def parse_difficulties(raw: str | None) -> frozenset[str]:
    if not raw:
        return frozenset()
    return frozenset(
        x.strip().lower() for x in raw.split(",") if x.strip()
    )


def should_enable_extended_thinking(
    *,
    evaluation_difficulty: str | None,
    attempt_number: int,
    enable_difficulties_raw: str,
    min_attempt: int,
) -> tuple[bool, str]:
    """返回 (enabled, reason)。"""
    enabled_diffs = parse_difficulties(enable_difficulties_raw)
    if evaluation_difficulty and evaluation_difficulty.lower() in enabled_diffs:
        return True, f"difficulty={evaluation_difficulty}"
    if min_attempt > 0 and attempt_number >= min_attempt:
        return True, f"attempt={attempt_number}>={min_attempt}"
    return False, "default_off"
