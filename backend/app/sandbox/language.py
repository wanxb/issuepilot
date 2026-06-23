"""Agent B 沙箱镜像的语言映射（2.1）。

GitHub primary_language 字段值 → 我们的 sandbox 镜像 key（小写、连字符无）。
镜像未实装时 fallback 到 python（最通用的 base，至少能跑 git/test）。
"""
from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


# GitHub 的 language 名（保留大小写）→ 我们的沙箱 lang key
# 镜像名形如 agent-sandbox-<lang_key>:latest
_LANGUAGE_TO_SANDBOX: dict[str, str] = {
    # node 镜像覆盖 JS/TS
    "JavaScript": "node",
    "TypeScript": "node",
    "Vue":        "node",
    "Svelte":     "node",

    # Python（base 镜像）
    "Python":     "python",
    "Cython":     "python",

    # Go
    "Go":         "go",

    # Rust
    "Rust":       "rust",

    # Java / JVM 系（暂走 java 镜像；Scala/Kotlin 后续再单建）
    "Java":       "java",
    "Kotlin":     "java",
    "Scala":      "java",
    "Groovy":     "java",
}

# 已实装的沙箱镜像 lang key
SUPPORTED_SANDBOXES: frozenset[str] = frozenset({
    "python", "node", "go", "rust", "java",
})

DEFAULT_SANDBOX_LANG = "python"


def resolve_sandbox_lang(primary_language: str | None) -> str:
    """primary_language → sandbox lang key。

    映射规则：
        1. 命中显式映射表 → 用映射值
        2. 已实装镜像的 lower(primary_language) 直接命中 → 用之
        3. 否则 fallback DEFAULT_SANDBOX_LANG（python）
    """
    if not primary_language:
        return DEFAULT_SANDBOX_LANG

    mapped = _LANGUAGE_TO_SANDBOX.get(primary_language)
    if mapped is not None:
        return mapped

    lowered = primary_language.lower()
    if lowered in SUPPORTED_SANDBOXES:
        return lowered

    log.info(
        "language.unmapped_fallback_to_python",
        primary_language=primary_language,
    )
    return DEFAULT_SANDBOX_LANG


def sandbox_image_for_language(
    *, image_prefix: str, primary_language: str | None,
    image_exists_check: object | None = None,
) -> tuple[str, str]:
    """返回 (image_tag, lang_key)。

    image_exists_check 可选 callable(image_name) -> bool；命中则用，否则
    继续 fallback 到 python 镜像。让单元测试可以注入 mock。
    """
    lang = resolve_sandbox_lang(primary_language)
    image = f"{image_prefix}-{lang}:latest"
    if image_exists_check is not None:
        try:
            ok = bool(image_exists_check(image))  # type: ignore[misc, operator]
        except Exception:
            ok = False
        if not ok:
            log.info(
                "language.image_missing_fallback",
                language=primary_language,
                missing=image,
            )
            return f"{image_prefix}-{DEFAULT_SANDBOX_LANG}:latest", DEFAULT_SANDBOX_LANG
    return image, lang
