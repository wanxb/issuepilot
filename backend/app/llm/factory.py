"""LLMClient 工厂：从 backend/config/models.yaml + .env 构造 FallbackLLMClient。

调用：
    client = build_client("agent_a")
    resp = await client.call(...)
    await client.aclose()

models.yaml 格式见 backend/config/models.example.yaml + docs/AGENT_DESIGN.md。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from app.core.config import get_settings
from app.llm.anthropic_client import AnthropicClient
from app.llm.base import LLMClient, RetryConfig
from app.llm.fallback import FallbackLLMClient

AgentName = Literal[
    "agent_a", "agent_b", "agent_c", "agent_d", "rejection_classifier",
]

_MODELS_YAML_CANDIDATES = (
    Path("backend/config/models.yaml"),
    Path("config/models.yaml"),
    Path(__file__).resolve().parent.parent.parent / "config" / "models.yaml",
)


class ModelsYamlError(RuntimeError):
    """models.yaml 缺失 / 格式错误 / agent 未配置。"""


@lru_cache(maxsize=1)
def _load_yaml() -> dict[str, Any]:
    path = _resolve_yaml_path()
    if path is None:
        # 没有 models.yaml 也允许工作：用 example.yaml 兜底，开发更友好
        example = (
            Path(__file__).resolve().parent.parent.parent
            / "config"
            / "models.example.yaml"
        )
        if not example.exists():
            raise ModelsYamlError("backend/config/models.yaml not found")
        path = example
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "agents" not in data:
        raise ModelsYamlError(f"{path} missing top-level 'agents'")
    return data


def _resolve_yaml_path() -> Path | None:
    for candidate in _MODELS_YAML_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def _env_or(key: str | None) -> str | None:
    """读取一个环境变量名，先查 Settings（.env），再查进程环境。

    SecretStr 字段必须直接读 attribute 才能拿明文（model_dump 会 mask 成 ****）。
    """
    if not key:
        return None
    settings = get_settings()
    attr_name = key.lower()
    val = getattr(settings, attr_name, None)
    if val is not None:
        if hasattr(val, "get_secret_value"):
            secret = val.get_secret_value()
            if secret:
                return secret
        elif isinstance(val, str) and val:
            return val
    return os.environ.get(key) or os.environ.get(attr_name) or None


def _build_primary(cfg: dict[str, Any]) -> LLMClient:
    provider = cfg["provider"]
    if provider in ("anthropic", "deepseek"):
        return AnthropicClient(
            model=cfg["model"],
            provider_label=provider,
            api_key=_env_or(cfg.get("api_key_env")),
            base_url=_env_or(cfg.get("base_url_env")) or cfg.get("base_url"),
            auth_token=_env_or(cfg.get("auth_token_env")),
            is_fallback=False,
        )
    raise ModelsYamlError(f"unsupported provider {provider!r}")


def _build_fallback(cfg: dict[str, Any]) -> LLMClient | None:
    fb = cfg.get("fallback") or {}
    if not fb.get("enabled"):
        return None
    provider = fb.get("provider", "deepseek")
    return AnthropicClient(
        model=fb["model"],
        provider_label=provider,
        api_key=_env_or(fb.get("api_key_env")),
        base_url=_env_or(fb.get("base_url_env")) or fb.get("base_url"),
        auth_token=_env_or(fb.get("auth_token_env")),
        is_fallback=True,
    )


def _build_retry(cfg: dict[str, Any]) -> RetryConfig:
    r = cfg.get("retry") or {}
    return RetryConfig(
        max_attempts=int(r.get("max_attempts", 3)),
        backoff_seconds=tuple(float(x) for x in r.get("backoff_seconds", [2, 5, 10])),
        retry_on_http=tuple(int(x) for x in r.get("retry_on_http", [408, 429, 500, 502, 503, 504])),
    )


def build_client(agent_name: AgentName) -> FallbackLLMClient:
    """根据 models.yaml 中的 agent 配置返回带 retry+fallback 的 client。"""
    data = _load_yaml()
    agents = data.get("agents", {})
    if agent_name not in agents:
        raise ModelsYamlError(f"agent {agent_name!r} not configured in models.yaml")
    cfg = agents[agent_name]

    return FallbackLLMClient(
        primary=_build_primary(cfg),
        fallback=_build_fallback(cfg),
        retry=_build_retry(cfg),
    )


def clear_cache() -> None:
    """测试 / 配置热加载时调用。"""
    _load_yaml.cache_clear()
