"""抓取领域白名单（4.x）。

只抓 3 个领域的仓库 / issue：
    - ai_agent  AI Agent 上下游：MCP / Agent 框架 / 多 agent / 工具调用 / RAG agent
    - llm       大模型相关：LLM / RAG / fine-tune / embedding / vector DB
    - robotics  机器人相关：ROS / 自动驾驶 / SLAM / 具身智能 / 强化学习

每个 domain：
    - topics：GitHub repo 的 topic tag 命中即算（高置信度信号）
    - keyword_re：name + description 正则匹配（兜底，topic 没打全时也能识别）

任意一项命中即视为该 domain。整个抓取流程在 CrawlerService 后置过滤：
不命中任何已配置 domain 的 repo / issue 直接丢弃，记 outcome.failures。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class DomainSpec:
    name: str
    topics: frozenset[str]
    keyword_re: re.Pattern[str]
    description: str


_AI_AGENT = DomainSpec(
    name="ai_agent",
    description="AI Agent 上下游（MCP / Agent 框架 / 多 agent / 工具调用）",
    topics=frozenset({
        # 核心 agent 相关
        "ai-agent", "ai-agents", "llm-agent", "llm-agents",
        "autonomous-agent", "autonomous-agents",
        "multi-agent", "multi-agents", "multi-agent-system",
        "agent-framework", "agentic", "agentic-ai", "agentic-framework",
        # MCP 协议
        "mcp", "mcp-server", "mcp-client",
        "model-context-protocol",
        # 主流 framework
        "langchain", "langgraph", "llamaindex",
        "semantic-kernel", "crewai", "autogen",
        "autogpt", "babyagi", "metagpt", "swarm",
        # 工具调用
        "tool-use", "function-calling", "tool-calling",
        # AI 助手
        "ai-assistant", "ai-copilot",
    }),
    keyword_re=re.compile(
        r"\b("
        r"ai[\s-]?agent|agentic|multi[\s-]?agent|autonomous[\s-]?agent|"
        r"agent[\s-]?framework|mcp[\s-]?server|mcp[\s-]?client|"
        r"model[\s-]?context[\s-]?protocol|"
        r"tool[\s-]?use|function[\s-]?calling|tool[\s-]?calling|"
        r"langchain|langgraph|llamaindex|crewai|autogen|metagpt|"
        r"autogpt|babyagi|"
        r"ai[\s-]?assistant|ai[\s-]?copilot"
        r")\b",
        re.IGNORECASE,
    ),
)

_LLM = DomainSpec(
    name="llm",
    description="大模型（LLM / RAG / fine-tune / embedding / vector DB）",
    topics=frozenset({
        # 核心 LLM
        "llm", "llms", "large-language-model", "large-language-models",
        "foundation-model", "foundation-models",
        # 主流模型 / 厂商
        "gpt", "gpt-4", "gpt-5", "chatgpt",
        "claude", "anthropic",
        "gemini", "openai",
        "llama", "llama2", "llama3",
        "mistral", "mixtral",
        "qwen", "deepseek",
        # RAG / 知识
        "rag", "retrieval-augmented-generation",
        "vector-database", "vector-search", "embeddings",
        "prompt-engineering", "prompt-tuning",
        # 训练 / 微调
        "fine-tuning", "lora", "qlora", "peft",
        "instruction-tuning", "rlhf",
        # 推理 / 服务
        "llm-inference", "llm-serving", "llm-ops",
        "inference-engine", "model-quantization",
        # 其他
        "transformer", "transformers", "nlp-machine-learning",
        "chatbot",
    }),
    keyword_re=re.compile(
        r"\b("
        r"llm|large[\s-]?language[\s-]?model|foundation[\s-]?model|"
        r"gpt-?[1-9]|chatgpt|"
        r"rag|retrieval[\s-]?augmented[\s-]?generation|"
        r"fine[\s-]?tuning|"
        r"vector[\s-]?database|vector[\s-]?search|embeddings?|"
        r"prompt[\s-]?engineering|"
        r"lora|qlora|peft|rlhf|"
        r"llm[\s-]?inference|llm[\s-]?serving|"
        r"transformer|"
        r"deepseek|qwen|mistral|llama-?[1-9]|"
        r"anthropic|openai|"
        r"chatbot"
        r")\b",
        re.IGNORECASE,
    ),
)

_ROBOTICS = DomainSpec(
    name="robotics",
    description="机器人（ROS / 自动驾驶 / SLAM / 具身智能 / 强化学习）",
    topics=frozenset({
        # 核心
        "robotics", "robot", "robots",
        # ROS
        "ros", "ros2", "ros-noetic", "ros-humble",
        # 移动 / 自动驾驶
        "autonomous-vehicles", "autonomous-driving", "self-driving",
        "self-driving-car", "drone", "drones", "uav",
        # 具身 / 形态
        "embodied-ai", "embodied-agent", "embodied-intelligence",
        "humanoid", "humanoid-robot",
        "quadruped", "quadruped-robot",
        "manipulation", "robotic-manipulation",
        # 算法 / 感知
        "slam", "visual-slam", "lidar", "lidar-slam",
        "motion-planning", "path-planning",
        "robot-learning", "robot-control",
        "reinforcement-learning-robotics",
        # 仿真
        "robot-simulation", "gazebo", "mujoco", "isaac-gym", "isaac-sim",
    }),
    keyword_re=re.compile(
        r"\b("
        r"robotics?|robots?|"
        r"ros2?|ros[\s-]?noetic|ros[\s-]?humble|"
        r"autonomous[\s-]?(vehicles?|driving)|self[\s-]?driving|"
        r"drones?|uav|"
        r"embodied[\s-]?(ai|agent|intelligence)|"
        r"humanoid|quadruped|"
        r"manipulation|"
        r"\bslam\b|visual[\s-]?slam|lidar|"
        r"motion[\s-]?planning|path[\s-]?planning|"
        r"robot[\s-]?(learning|control|simulation)|"
        r"gazebo|mujoco|isaac[\s-]?(gym|sim)"
        r")\b",
        re.IGNORECASE,
    ),
)


DOMAIN_REGISTRY: dict[str, DomainSpec] = {
    _AI_AGENT.name: _AI_AGENT,
    _LLM.name: _LLM,
    _ROBOTICS.name: _ROBOTICS,
}


KNOWN_DOMAINS: frozenset[str] = frozenset(DOMAIN_REGISTRY.keys())


# ---------------------------------------------------------------------------
# match / 体检
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DomainMatch:
    """返回结构。matched_domain 为 None 表示不命中任何。"""
    matched_domain: str | None
    signal: str             # "topic:ai-agent" / "keyword:rag" / "none"


def match_repo(
    *,
    name: str,
    description: str | None,
    topics: Iterable[str] | None,
    accepted_domains: Iterable[str],
) -> DomainMatch:
    """对单个 repo 元数据做 domain 命中检查。

    accepted_domains 是 caller 当前 crawl_target 配置允许的 domain 子集，
    传 'all' 字符串视为接受全部已知 domain（少用）。
    """
    accepted_set = set(accepted_domains)
    if "all" in accepted_set:
        accepted_set = set(KNOWN_DOMAINS)
    accepted_set &= KNOWN_DOMAINS

    topic_lower = {t.lower() for t in (topics or []) if isinstance(t, str)}
    text = f"{name or ''} {description or ''}"

    # 优先 topic 命中（高置信度）
    for dname in accepted_set:
        spec = DOMAIN_REGISTRY[dname]
        hit = topic_lower & spec.topics
        if hit:
            return DomainMatch(
                matched_domain=dname,
                signal=f"topic:{next(iter(sorted(hit)))}",
            )

    # keyword 兜底
    for dname in accepted_set:
        spec = DOMAIN_REGISTRY[dname]
        m = spec.keyword_re.search(text)
        if m:
            return DomainMatch(
                matched_domain=dname,
                signal=f"keyword:{m.group(0).lower()}",
            )

    return DomainMatch(matched_domain=None, signal="none")


def validate_domains(raw: Iterable[str]) -> list[str]:
    """spec 校验时调用：去重 + 校验所有 domain 都已知。"""
    out: list[str] = []
    seen: set[str] = set()
    for d in raw:
        if not isinstance(d, str):
            raise ValueError(f"domain must be string, got {type(d).__name__}")
        d = d.strip().lower()
        if d in seen:
            continue
        if d != "all" and d not in KNOWN_DOMAINS:
            raise ValueError(
                f"unknown domain {d!r}; known: {sorted(KNOWN_DOMAINS)}",
            )
        seen.add(d)
        out.append(d)
    if not out:
        raise ValueError("domains list cannot be empty")
    return out


def build_github_search_query(
    *,
    domains: list[str],
    extra_qualifiers: str | None = None,
) -> str:
    """把 accepted domains 拼成 GitHub Search API 的 q 参数。

    GitHub Search API 硬限制：q 中 AND/OR/NOT 总数 ≤ 5 → 最多 6 个 topic
    串成 OR。多 domain 时按"每 domain 各取 1-2 个 flagship topic"原则均分。

    extra_qualifiers：调用方追加的 `stars:>500 pushed:>2026-01-01` 等。
    """
    if "all" in domains:
        domains = sorted(KNOWN_DOMAINS)

    # GitHub Search API max 5 OR → max 6 topics
    MAX_TOPICS = 6
    per_domain = max(1, MAX_TOPICS // max(len(domains), 1))
    topic_terms: list[str] = []
    for dname in domains:
        spec = DOMAIN_REGISTRY.get(dname)
        if spec is None:
            continue
        chosen = _FLAGSHIP_TOPICS.get(dname, sorted(spec.topics))[:per_domain]
        topic_terms.extend(f"topic:{t}" for t in chosen)
        if len(topic_terms) >= MAX_TOPICS:
            break
    topic_terms = topic_terms[:MAX_TOPICS]

    or_clause = " OR ".join(topic_terms) if topic_terms else "topic:llm"
    q = or_clause
    if extra_qualifiers:
        q = f"{q} {extra_qualifiers}".strip()
    return q


# 每 domain 的"旗舰" topic：覆盖度最高、含 stars 最多
_FLAGSHIP_TOPICS: dict[str, list[str]] = {
    "ai_agent": ["ai-agent", "mcp-server", "agentic", "multi-agent"],
    "llm":      ["llm", "rag", "transformer", "vector-database"],
    "robotics": ["robotics", "ros2", "slam", "embodied-ai"],
}
