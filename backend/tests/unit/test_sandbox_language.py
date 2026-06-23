"""Agent B 沙箱镜像语言映射（2.1）单测。"""
from __future__ import annotations

import pytest

from app.sandbox.language import (
    DEFAULT_SANDBOX_LANG,
    SUPPORTED_SANDBOXES,
    resolve_sandbox_lang,
    sandbox_image_for_language,
)


class TestResolveSandboxLang:
    @pytest.mark.parametrize("primary,expected", [
        ("Python",     "python"),
        ("Cython",     "python"),
        ("JavaScript", "node"),
        ("TypeScript", "node"),
        ("Vue",        "node"),
        ("Svelte",     "node"),
        ("Go",         "go"),
        ("Rust",       "rust"),
        ("Java",       "java"),
        ("Kotlin",     "java"),
        ("Scala",      "java"),
        ("Groovy",     "java"),
    ])
    def test_known_languages(self, primary: str, expected: str) -> None:
        assert resolve_sandbox_lang(primary) == expected

    @pytest.mark.parametrize("primary", ["C", "C++", "Ruby", "Elixir", "Zig"])
    def test_unmapped_falls_back_to_python(self, primary: str) -> None:
        assert resolve_sandbox_lang(primary) == DEFAULT_SANDBOX_LANG

    @pytest.mark.parametrize("v", [None, ""])
    def test_empty_falls_back_to_python(self, v: str | None) -> None:
        assert resolve_sandbox_lang(v) == DEFAULT_SANDBOX_LANG

    def test_lowercase_passthrough_for_supported(self) -> None:
        # 如果 caller 已经给出 lowercase sandbox key，应直接接受
        for lang in SUPPORTED_SANDBOXES:
            assert resolve_sandbox_lang(lang) == lang


class TestSandboxImageForLanguage:
    def test_no_check_returns_canonical_image(self) -> None:
        image, lang = sandbox_image_for_language(
            image_prefix="agent-sandbox", primary_language="Go",
        )
        assert image == "agent-sandbox-go:latest"
        assert lang == "go"

    def test_image_exists_check_ok_returns_canonical(self) -> None:
        image, lang = sandbox_image_for_language(
            image_prefix="agent-sandbox", primary_language="Rust",
            image_exists_check=lambda _: True,
        )
        assert image == "agent-sandbox-rust:latest"
        assert lang == "rust"

    def test_image_missing_falls_back_to_python(self) -> None:
        image, lang = sandbox_image_for_language(
            image_prefix="agent-sandbox", primary_language="Java",
            image_exists_check=lambda _: False,
        )
        assert image == "agent-sandbox-python:latest"
        assert lang == "python"

    def test_image_check_raises_treated_as_missing(self) -> None:
        def boom(_: str) -> bool:
            raise RuntimeError("docker daemon down")
        image, lang = sandbox_image_for_language(
            image_prefix="agent-sandbox", primary_language="Go",
            image_exists_check=boom,
        )
        # 异常视为镜像不存在；fallback python（不让任务挂在 docker API 问题上）
        assert image == "agent-sandbox-python:latest"
        assert lang == "python"

    def test_python_repo_no_fallback_when_python_exists(self) -> None:
        seen: list[str] = []

        def check(name: str) -> bool:
            seen.append(name)
            return True

        image, lang = sandbox_image_for_language(
            image_prefix="agent-sandbox", primary_language="Python",
            image_exists_check=check,
        )
        assert image == "agent-sandbox-python:latest"
        assert lang == "python"
        assert seen == ["agent-sandbox-python:latest"]

    def test_custom_prefix_respected(self) -> None:
        image, _ = sandbox_image_for_language(
            image_prefix="myorg/agent-sb", primary_language="Go",
        )
        assert image == "myorg/agent-sb-go:latest"


class TestSupportedSandboxes:
    def test_default_lang_is_supported(self) -> None:
        assert DEFAULT_SANDBOX_LANG in SUPPORTED_SANDBOXES

    def test_supported_set_matches_2_1_milestone(self) -> None:
        """2.1 ROADMAP 承诺 5 个镜像（含 python）—— 任何变更要本测同步。"""
        assert SUPPORTED_SANDBOXES == {
            "python", "node", "go", "rust", "java",
        }
