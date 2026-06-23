#!/usr/bin/env python3
"""3.2: devcontainer.json 轻量支持。

由 entrypoint.sh 在 clone 后调用：检测 /workspace/.devcontainer/devcontainer.json
（或仓库根 .devcontainer.json），解析有用字段，输出 shell 友好的
"key=value" 行让 entrypoint.sh 用 `source` 后续步骤参考。

完整 devcontainer 规范涉及独立容器、features、image override 等，超出
本沙箱单容器范畴。我们只支持最常用、能就地执行的子集：
    - containerEnv          → 注入到沙箱 env
    - postCreateCommand     → 在 Agent B 启动前跑一次（string 或 list）
    - postStartCommand      → 同上（很多 repo 把 setup 放这里）

不支持的（输出 warning，不阻塞）：
    - image / dockerfile / features / forwardPorts / build
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

_CANDIDATES = (
    Path("/workspace/.devcontainer/devcontainer.json"),
    Path("/workspace/.devcontainer.json"),
)


def _strip_jsonc(text: str) -> str:
    """devcontainer.json 实际是 JSONC：去掉 // 和 /* */ 注释 + 末逗号。"""
    out: list[str] = []
    i = 0
    in_str = False
    str_quote = ""
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == str_quote:
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            str_quote = '"'
            out.append(ch)
            i += 1
            continue
        # 行注释
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        # 块注释
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    cleaned = "".join(out)
    # 末逗号
    import re
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    return cleaned


def _load() -> dict[str, Any] | None:
    for p in _CANDIDATES:
        if p.exists():
            try:
                txt = p.read_text(encoding="utf-8")
                data = json.loads(_strip_jsonc(txt))
                if isinstance(data, dict):
                    return data
            except Exception as e:
                print(f"=== DEVCONTAINER: parse failed for {p}: {e} ===", file=sys.stderr)
                return None
    return None


def _flatten_cmd(cmd: Any) -> str | None:
    """devcontainer postCreateCommand 支持 string / list / dict。"""
    if isinstance(cmd, str):
        return cmd.strip() or None
    if isinstance(cmd, list):
        return " ".join(shlex.quote(str(x)) for x in cmd) or None
    if isinstance(cmd, dict):
        # 多命令：{"name1": "cmd1", ...} —— 拼成 && 序列
        parts = [str(v).strip() for v in cmd.values() if v]
        return " && ".join(parts) or None
    return None


def main() -> int:
    data = _load()
    if data is None:
        print("HAS_DEVCONTAINER=0")
        return 0

    print("HAS_DEVCONTAINER=1")
    name = data.get("name") or ""
    if name:
        print(f"DEVCONTAINER_NAME={shlex.quote(str(name))}")

    # containerEnv: 注入到当前 shell；用 export 行
    env = data.get("containerEnv") or {}
    if isinstance(env, dict):
        for k, v in env.items():
            if isinstance(k, str) and k.isidentifier() and isinstance(v, (str, int, float)):
                print(f"export {k}={shlex.quote(str(v))}")

    # post-create / post-start commands 拼成单行让 entrypoint.sh 直接 eval
    for field in ("postCreateCommand", "postStartCommand"):
        cmd = _flatten_cmd(data.get(field))
        if cmd:
            # 拆成单独的 env，让 entrypoint.sh 按顺序运行
            var = f"DEVCONTAINER_{field.upper()}"
            print(f"{var}={shlex.quote(cmd)}")

    # 警告 unsupported fields
    unsupported = [
        k for k in ("image", "dockerfile", "build", "features", "forwardPorts")
        if k in data
    ]
    if unsupported:
        print(
            "=== DEVCONTAINER: unsupported fields (ignored): "
            f"{', '.join(unsupported)} ===",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
