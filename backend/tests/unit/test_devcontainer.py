"""sandbox/shared/apply_devcontainer.py 解析逻辑（3.2d）单测。

不真起容器，只测 JSONC 剥注释 + 命令扁平化 + 输出格式。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_module():
    """从 sandbox/shared/apply_devcontainer.py 直接 import（不属 backend/app）。

    路径解析：host 跑（pytest from backend/）/ container 跑（/app）/ 直接 cd 跑都得能找到。
    """
    candidates = [
        # host: backend/tests/unit/ → repo/sandbox/shared/
        Path(__file__).resolve().parents[3] / "sandbox" / "shared" / "apply_devcontainer.py",
        # container: /app + relative ../sandbox/shared/
        Path("/sandbox/shared/apply_devcontainer.py"),
        # 容器内挂 /scripts 时同级
        Path("/scripts/../sandbox/shared/apply_devcontainer.py"),
    ]
    script = next((c for c in candidates if c.exists()), None)
    if script is None:
        import pytest
        pytest.skip(
            "apply_devcontainer.py not reachable from test runner "
            "(expected when container only mounts backend/)"
        )
    spec = importlib.util.spec_from_file_location("apply_devcontainer", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def m():
    return _load_module()


class TestStripJsonc:
    def test_line_comment_removed(self, m) -> None:
        out = m._strip_jsonc('{"a": 1 // comment\n, "b": 2}')
        assert "// comment" not in out

    def test_block_comment_removed(self, m) -> None:
        out = m._strip_jsonc('{/* hi */"a":1}')
        assert "hi" not in out

    def test_trailing_comma_removed(self, m) -> None:
        out = m._strip_jsonc('{"a": 1,}')
        # 末逗号去掉后应该是合法 JSON
        import json
        assert json.loads(out) == {"a": 1}

    def test_string_with_comment_chars_preserved(self, m) -> None:
        """字符串里的 // 不应被当注释剥掉。"""
        out = m._strip_jsonc('{"url": "https://example.com/path"}')
        import json
        assert json.loads(out) == {"url": "https://example.com/path"}

    def test_escaped_quote_in_string(self, m) -> None:
        out = m._strip_jsonc(r'{"msg": "say \"hi\""}')
        import json
        assert json.loads(out)["msg"] == 'say "hi"'


class TestFlattenCmd:
    def test_string(self, m) -> None:
        assert m._flatten_cmd("pip install -e .") == "pip install -e ."

    def test_list(self, m) -> None:
        assert m._flatten_cmd(["npm", "install"]) == "npm install"

    def test_list_with_special_chars(self, m) -> None:
        out = m._flatten_cmd(["echo", "hello world"])
        # shlex.quote 会加引号
        assert "hello world" in out
        assert out.startswith("echo")

    def test_dict_joined(self, m) -> None:
        out = m._flatten_cmd({"setup": "pip install", "build": "make"})
        assert "pip install" in out and "make" in out and "&&" in out

    def test_empty_returns_none(self, m) -> None:
        assert m._flatten_cmd("") is None
        assert m._flatten_cmd([]) is None
        assert m._flatten_cmd({}) is None
        assert m._flatten_cmd(None) is None
