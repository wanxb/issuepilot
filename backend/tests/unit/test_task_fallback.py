"""task 级 fallback（2.3）：dev_worker.build_sandbox_llm_env 切换语义。

不依赖真实 Settings；构造一个最小 fake Settings 验证 primary / fallback
环境变量构造正确。models.yaml 由真实文件提供（已在容器内）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.workers.dev_worker import build_sandbox_llm_env


class _Sec:
    def __init__(self, v: str) -> None:
        self._v = v

    def get_secret_value(self) -> str:
        return self._v


def _fake_settings(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    auth_token: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        anthropic_api_key=_Sec(api_key) if api_key else None,
        anthropic_base_url=base_url,
        anthropic_auth_token=_Sec(auth_token) if auth_token else None,
    )


class TestPrimary:
    def test_primary_returns_sonnet(self) -> None:
        env, model = build_sandbox_llm_env(
            _fake_settings(api_key="sk-xx"),
            use_fallback=False,
        )
        assert model == "claude-sonnet-4-6"
        assert env["ANTHROPIC_API_KEY"] == "sk-xx"

    def test_primary_only_includes_set_fields(self) -> None:
        env, _ = build_sandbox_llm_env(_fake_settings(), use_fallback=False)
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_BASE_URL" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env

    def test_primary_auth_token_mode(self) -> None:
        env, _ = build_sandbox_llm_env(
            _fake_settings(base_url="https://proxy.example.com",
                           auth_token="proxy-tok"),
            use_fallback=False,
        )
        assert env["ANTHROPIC_BASE_URL"] == "https://proxy.example.com"
        assert env["ANTHROPIC_AUTH_TOKEN"] == "proxy-tok"
        assert "ANTHROPIC_API_KEY" not in env


class TestFallback:
    def test_fallback_returns_deepseek(self) -> None:
        # 容器内 models.yaml 配了 agent_b.fallback enabled=true + DeepSeek
        # 但 DEEPSEEK_ANTHROPIC_TOKEN env 在 test 容器里若未设，会回落 primary
        env, model = build_sandbox_llm_env(
            _fake_settings(api_key="sk-primary"),
            use_fallback=True,
        )
        # 两种合法结果：env 完整 → DeepSeek 模型；token 缺失 → 回落 primary
        if model == "DeepSeek-V4-Pro":
            assert env["ANTHROPIC_BASE_URL"].startswith("https://api.deepseek.com")
            assert env.get("ANTHROPIC_AUTH_TOKEN")
            assert "ANTHROPIC_API_KEY" not in env
        else:
            assert model == "claude-sonnet-4-6"  # primary 回落
            assert env.get("ANTHROPIC_API_KEY") == "sk-primary"


@pytest.mark.parametrize("flag", [True, False])
def test_no_crash_with_empty_settings(flag: bool) -> None:
    """两条路径都必须能在 settings 全空时返回（不抛）。"""
    env, model = build_sandbox_llm_env(_fake_settings(), use_fallback=flag)
    assert isinstance(env, dict)
    assert isinstance(model, str)
