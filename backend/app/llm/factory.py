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
from app.llm.openai_client import OpenAIClient

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
    return _build_client(cfg, is_fallback=False)


def _build_primary_chain(cfg: dict[str, Any]) -> list[LLMClient]:
    """4.x: 同 provider 多 model 轮换。

    cfg.model_fallbacks（可选）：list[str]，同 provider 下额外尝试的模型。
    返回 [primary_client, model_fallback_1_client, ...]，is_fallback=False 全程。
    向后兼容：缺省时返回单元素 [primary_client]。
    """
    chain: list[LLMClient] = [_build_primary(cfg)]
    extra_models = cfg.get("model_fallbacks") or []
    if not isinstance(extra_models, list):
        raise ModelsYamlError("model_fallbacks must be a list of model strings")
    for model_name in extra_models:
        if not isinstance(model_name, str) or not model_name:
            raise ModelsYamlError(
                f"model_fallbacks entries must be non-empty strings, got {model_name!r}",
            )
        # 同 provider，只换 model；其他 auth/base_url 完全复用
        rotated_cfg = {**cfg, "model": model_name}
        chain.append(_build_client(rotated_cfg, is_fallback=False))
    return chain


def _build_fallback(cfg: dict[str, Any]) -> LLMClient | None:
    fb = cfg.get("fallback") or {}
    if not fb.get("enabled"):
        return None
    return _build_client(fb, is_fallback=True)


def _build_client(cfg: dict[str, Any], *, is_fallback: bool) -> LLMClient:
    """统一构造（primary + fallback 同 schema）。

    provider 取值：
        - anthropic              Anthropic Messages API（用 anthropic SDK）
        - deepseek               DeepSeek 走 Anthropic 兼容接口（用 anthropic SDK
                                 + 自定义 base_url）—— 这是历史路径，验证可用
        - openai                 OpenAI Chat Completions（用 httpx）
        - deepseek_openai        DeepSeek 走 OpenAI 兼容接口（用 OpenAIClient
                                 + DeepSeek base_url）
        - 任何其他 OpenAI 兼容厂商（智谱/Moonshot/通义）填 provider=openai +
          base_url 即可
    """
    provider = cfg.get("provider", "")
    label = cfg.get("provider_label") or provider  # 区分 deepseek 走哪条路
    if provider in ("anthropic", "deepseek"):
        return AnthropicClient(
            model=cfg["model"],
            provider_label=label,
            api_key=_env_or(cfg.get("api_key_env")),
            base_url=_env_or(cfg.get("base_url_env")) or cfg.get("base_url"),
            auth_token=_env_or(cfg.get("auth_token_env")),
            is_fallback=is_fallback,
        )
    if provider in ("openai", "deepseek_openai"):
        return OpenAIClient(
            model=cfg["model"],
            provider_label=label,
            api_key=_env_or(cfg.get("api_key_env")),
            base_url=_env_or(cfg.get("base_url_env")) or cfg.get("base_url"),
            is_fallback=is_fallback,
        )
    raise ModelsYamlError(f"unsupported provider {provider!r}")


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
        primary=_build_primary_chain(cfg),
        fallback=_build_fallback(cfg),
        retry=_build_retry(cfg),
    )


def clear_cache() -> None:
    """测试 / 配置热加载时调用。"""
    _load_yaml.cache_clear()
