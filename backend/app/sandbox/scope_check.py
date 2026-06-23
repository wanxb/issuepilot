"""修改影响范围检查（3.2）。

dev_task.files_changed 与 issue body / evaluation summary 提到的文件路径
是否有重合？无重合 → 提示 Agent C "scope_warning"（可能是 scope_creep）。

启发式：
    - 从 issue body + evaluation.summary 抽取看起来像文件路径的 token
      （含 / 或 . 后缀的，且不是 URL / 邮箱）
    - 对比 dev_task.files_changed
    - 完全无重叠 → suspicious=True；部分重叠 → matched files；全无 → no_signal
"""
from __future__ import annotations

import re
from typing import Any

_FILE_PATH_RE = re.compile(
    r"""(?<![/\w@:])              # 不接在 url / 邮箱里
        ([A-Za-z0-9_][\w./-]{1,200}\.[A-Za-z0-9_]{1,8})  # path 含点扩展
    """,
    re.VERBOSE,
)

# 常见 false-positive：包名 / 文档术语
_BLACKLIST: frozenset[str] = frozenset({
    "e.g.", "i.e.", "etc.", "v1.0", "v2.0", "v3.0",
    "ubuntu.com", "github.com", "google.com",
    "min.io", "node.js",
})


def extract_paths(text: str | None) -> set[str]:
    """从自由文本里抽看起来是文件路径的 token。"""
    if not text:
        return set()
    out: set[str] = set()
    for m in _FILE_PATH_RE.finditer(text):
        p = m.group(1).strip(".,;:!?)\"'")
        if p in _BLACKLIST or len(p) < 4:
            continue
        out.add(p)
    return out


def _basename(p: str) -> str:
    return p.rsplit("/", 1)[-1]


def scope_check(
    *,
    files_changed: list[str] | None,
    issue_body: str | None,
    evaluation_summary: str | None,
) -> dict[str, Any]:
    """返回 dict 给 dev_task.scope_check / Agent C prompt：

        {
          "suspicious": bool,
          "files_changed": list[str],
          "referenced_in_issue": list[str],
          "matched": list[str],
        }

    suspicious=True 触发条件：
        - files_changed 非空
        - 从 issue/evaluation 抽到 ≥1 个 path
        - 完全无 basename 重叠
    """
    files = list(files_changed or [])
    referenced = (
        extract_paths(issue_body) | extract_paths(evaluation_summary)
    )

    matched: list[str] = []
    if files and referenced:
        ref_bases = {_basename(p).lower() for p in referenced}
        for f in files:
            if _basename(f).lower() in ref_bases:
                matched.append(f)

    suspicious = bool(files and referenced and not matched)
    return {
        "suspicious": suspicious,
        "files_changed": files,
        "referenced_in_issue": sorted(referenced),
        "matched": matched,
    }
