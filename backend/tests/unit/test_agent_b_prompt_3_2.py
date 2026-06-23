"""Agent B prompt v1.1（3.2）变更校验。"""
from __future__ import annotations


def test_prompt_version_bumped() -> None:
    """3.2 提示策略变更必须配 prompt_version bump。"""
    from app.agents.prompts.agent_b import PROMPT_VERSION
    assert PROMPT_VERSION == "1.1"


def test_devcontainer_env_var_hinted() -> None:
    """SYSTEM_PROMPT 需要告诉 Agent B 读 AGENT_B_HAS_DEVCONTAINER。"""
    from app.agents.prompts.agent_b import SYSTEM_PROMPT
    assert "AGENT_B_HAS_DEVCONTAINER" in SYSTEM_PROMPT


def test_multi_tier_test_strategy_present() -> None:
    """TEST 阶段必须含 Tier 1/2/3 三级。"""
    from app.agents.prompts.agent_b import SYSTEM_PROMPT
    assert "Tier 1" in SYSTEM_PROMPT
    assert "Tier 2" in SYSTEM_PROMPT
    assert "Tier 3" in SYSTEM_PROMPT


def test_tier_3_external_service_skip_guidance() -> None:
    """Tier 3 必须告诉 Agent B 遇到外部服务依赖时 SKIP（避免卡死沙箱）。"""
    from app.agents.prompts.agent_b import SYSTEM_PROMPT
    assert "SKIP" in SYSTEM_PROMPT or "skip" in SYSTEM_PROMPT


def test_legacy_one_size_fits_all_replaced() -> None:
    """旧的 'Run the full test suite' 一刀切提示应被多步策略替代。"""
    from app.agents.prompts.agent_b import SYSTEM_PROMPT
    # 旧版有 "Run the full test suite: `python -m pytest -q`" 单行
    # 新版改为 Tier 1 起跑touching modules
    assert "touching the modules you changed" in SYSTEM_PROMPT


def test_build_prompt_still_works() -> None:
    """schema 没变，已有 build_prompt() 不能挂。"""
    from app.agents.prompts.agent_b import build_prompt
    from app.agents.schemas import AgentBInput
    inp = AgentBInput(
        issue_title="t", issue_body="b", issue_url="https://github.com/o/r/issues/1",
        repo_full_name="o/r", evaluation_summary="x",
        attempt_number=1, branch_name="b", forked_repo="o/r",
    )
    s = build_prompt(inp)
    assert "Begin with ANALYZE" in s
    assert "o/r" in s
