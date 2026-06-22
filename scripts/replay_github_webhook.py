"""GitHub Webhook 本地回放工具（里程碑 2.3 起步项）。

为什么需要它：
    Phase 1 dry run 报告里 PR 真实创建 + Webhook 真实接收两段未走通——
    前者需用户升级 dev_token；后者历史上只能等 GitHub 真的发生 close/merge 才
    能验证端到端。这个脚本让 Webhook 路径在本地完全可复现：手动构造 GitHub
    格式的 payload，HMAC 签好 POST 到本地 /api/v1/webhooks/github，整数据
    保真度等价于 GitHub 真实推送。

也能拉真实 GitHub 数据（--from-github + GITHUB_TOKEN）作为 payload，进一步
确保字段结构与 GitHub 推送完全一致。

用法：
    # 列出当前数据库中有 PR 的 issue（通过 /api/v1/issues 接口）
    python scripts/replay_github_webhook.py list

    # 回放一个 PR merged 事件
    python scripts/replay_github_webhook.py merged \
        --pr-url https://github.com/o/r/pull/1 --actor alice

    # 回放 PR closed（未 merge）
    python scripts/replay_github_webhook.py closed \
        --pr-url https://github.com/o/r/pull/1 --actor maintainer-bob

    # 回放 review submitted
    python scripts/replay_github_webhook.py review \
        --pr-url https://github.com/o/r/pull/1 \
        --state changes_requested --body "Please add tests"

    # 回放 PR comment
    python scripts/replay_github_webhook.py comment \
        --pr-url https://github.com/o/r/pull/1 --body "LGTM"

    # ping（GitHub admin 测试连通性）
    python scripts/replay_github_webhook.py ping

    # 用真实 GitHub PR 数据当 payload（保真度最高）
    python scripts/replay_github_webhook.py merged \
        --pr-url https://github.com/o/r/pull/1 --from-github

环境变量：
    GITHUB_WEBHOOK_SECRET   必填——HMAC 签名密钥（与 backend Settings 同名）
    GITHUB_TOKEN            可选——配合 --from-github 拉真实 PR 元数据
    ISSUEPILOT_BASE_URL     可选——默认 http://localhost:8000

输出：HTTP 状态码 + 响应 JSON（pretty）。非 2xx 时 exit 1。
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# .env 加载（轻量，避免依赖 python-dotenv）
# ---------------------------------------------------------------------------


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# ---------------------------------------------------------------------------
# PR URL 解析
# ---------------------------------------------------------------------------


def _parse_pr_url(url: str) -> tuple[str, str, int]:
    """https://github.com/{owner}/{repo}/pull/{n} → (owner, repo, n)。"""
    prefix = "https://github.com/"
    if not url.startswith(prefix):
        raise SystemExit(f"--pr-url 必须以 {prefix} 开头：{url}")
    rest = url[len(prefix):]
    parts = rest.split("/")
    if len(parts) < 4 or parts[2] != "pull":
        raise SystemExit(f"无法解析 PR URL：{url}")
    owner, repo, _, num_s = parts[0], parts[1], parts[2], parts[3]
    try:
        num = int(num_s)
    except ValueError as e:
        raise SystemExit(f"PR 号非整数：{num_s}") from e
    return owner, repo, num


# ---------------------------------------------------------------------------
# Payload 构造（GitHub 真实字段结构的最小子集 + 部分常见冗余字段）
# 字段子集对齐 backend/app/api/webhooks.py 的实际访问路径。
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _base_pr_block(pr_url: str, *, merged: bool, sha: str | None = None,
                   closed_at: str | None = None,
                   merged_at: str | None = None,
                   merged_by: str | None = None) -> dict[str, Any]:
    owner, repo, num = _parse_pr_url(pr_url)
    block: dict[str, Any] = {
        "html_url": pr_url,
        "number": num,
        "state": "closed",
        "merged": merged,
        "title": "(replay) PR closed",
        "body": "(replay)",
        "base": {
            "ref": "main",
            "repo": {"full_name": f"{owner}/{repo}"},
        },
        "head": {"ref": "replay/branch", "repo": {"full_name": f"{owner}/{repo}"}},
        "closed_at": closed_at or _now_iso(),
        "merged_at": merged_at if merged else None,
        "merge_commit_sha": sha if merged else None,
        "merged_by": {"login": merged_by} if (merged and merged_by) else None,
    }
    return block


def build_pr_merged_payload(pr_url: str, *, actor: str,
                            sha: str | None = None) -> dict[str, Any]:
    now = _now_iso()
    return {
        "action": "closed",
        "pull_request": _base_pr_block(
            pr_url, merged=True,
            sha=sha or "deadbeefcafe1234567890abcdef1234567890ab",
            closed_at=now, merged_at=now, merged_by=actor,
        ),
        "sender": {"login": actor},
        "repository": _repo_block(pr_url),
    }


def build_pr_closed_payload(pr_url: str, *, actor: str) -> dict[str, Any]:
    now = _now_iso()
    return {
        "action": "closed",
        "pull_request": _base_pr_block(pr_url, merged=False, closed_at=now),
        "sender": {"login": actor},
        "repository": _repo_block(pr_url),
    }


def build_review_payload(pr_url: str, *, reviewer: str, state: str,
                         body: str | None) -> dict[str, Any]:
    owner, repo, num = _parse_pr_url(pr_url)
    return {
        "action": "submitted",
        "pull_request": {
            "html_url": pr_url, "number": num,
            "base": {"ref": "main", "repo": {"full_name": f"{owner}/{repo}"}},
        },
        "review": {
            "state": state,
            "body": body,
            "submitted_at": _now_iso(),
            "user": {"login": reviewer},
        },
        "sender": {"login": reviewer},
        "repository": _repo_block(pr_url),
    }


def build_comment_payload(pr_url: str, *, commenter: str,
                          body: str) -> dict[str, Any]:
    return {
        "action": "created",
        "issue": {
            "pull_request": {"html_url": pr_url},
            "html_url": pr_url,
        },
        "comment": {
            "body": body,
            "created_at": _now_iso(),
            "user": {"login": commenter},
        },
        "sender": {"login": commenter},
        "repository": _repo_block(pr_url),
    }


def build_ping_payload() -> dict[str, Any]:
    return {"zen": "Anything added dilutes everything else.", "hook_id": 0}


def _repo_block(pr_url: str) -> dict[str, Any]:
    owner, repo, _ = _parse_pr_url(pr_url)
    return {"full_name": f"{owner}/{repo}", "name": repo,
            "owner": {"login": owner}}


# ---------------------------------------------------------------------------
# --from-github：拉真实 PR / Review / Comment 数据
# 用 stdlib urllib 避免给脚本拉 httpx 依赖。
# ---------------------------------------------------------------------------


def _gh_get(path: str, token: str | None) -> dict[str, Any]:
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "IssuePilot-Replay/0.1",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_real_pr_block(pr_url: str, token: str | None) -> dict[str, Any]:
    owner, repo, num = _parse_pr_url(pr_url)
    return _gh_get(f"/repos/{owner}/{repo}/pulls/{num}", token)


# ---------------------------------------------------------------------------
# 签名 + POST
# ---------------------------------------------------------------------------


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


def post_webhook(*, base_url: str, event: str, payload: dict[str, Any],
                 secret: str, delivery: str = "replay-001") -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": sign(secret, body),
        "User-Agent": "GitHub-Hookshot/replay",
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/v1/webhooks/github",
        data=body, headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise SystemExit(f"连不上 {base_url}：{e.reason}") from e


# ---------------------------------------------------------------------------
# list 子命令：查询本地 API 列出有 PR 的 issue
# ---------------------------------------------------------------------------


def cmd_list(base_url: str) -> None:
    url = f"{base_url.rstrip('/')}/api/v1/issues?limit=50"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise SystemExit(f"连不上 {base_url}：{e.reason}") from e

    items = data.get("items") or []
    rows = [i for i in items if i.get("pull_request")]
    if not rows:
        print("(没有有 PR 的 issue。可手动 INSERT pull_requests 行用于测试。)")
        return
    print(f"{'STATUS':<14}  {'PR':<5}  {'OUTCOME':<22}  URL")
    for i in rows:
        pr = i["pull_request"]
        url = pr.get("github_pr_url") or pr.get("url") or ""
        num = pr.get("github_pr_number") or pr.get("number") or "?"
        print(f"{i['status']:<14}  #{num:<4}  "
              f"{str(pr.get('final_outcome') or '-'):<22}  {url}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _get_secret(arg: str | None) -> str:
    s = arg or os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if not s:
        raise SystemExit(
            "缺少 webhook secret。设 GITHUB_WEBHOOK_SECRET 或传 --secret。"
        )
    return s


def main() -> None:
    parser = argparse.ArgumentParser(
        description="本地回放 GitHub Webhook 推送（HMAC 签名 + POST 到本地 API）。",
    )
    parser.add_argument("--base-url",
                        default=os.environ.get("ISSUEPILOT_BASE_URL",
                                               "http://localhost:8000"),
                        help="后端 URL（默认 http://localhost:8000）")
    parser.add_argument("--secret", help="GITHUB_WEBHOOK_SECRET 覆盖")
    parser.add_argument("--delivery", default="replay-001",
                        help="X-GitHub-Delivery 头（默认 replay-001）")

    sub = parser.add_subparsers(dest="event", required=True)

    p_list = sub.add_parser("list", help="列出本地有 PR 的 issue")

    for name, help_ in (("merged", "回放 PR merged"),
                        ("closed", "回放 PR closed（未 merge）")):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--pr-url", required=True)
        sp.add_argument("--actor", default="replay-maintainer",
                        help="merger / closer login（默认 replay-maintainer）")
        sp.add_argument("--sha", help="merge_commit_sha（仅 merged）")
        sp.add_argument("--from-github", action="store_true",
                        help="拉真实 PR 数据当 payload 主体（需 GITHUB_TOKEN）")

    sp_r = sub.add_parser("review", help="回放 review submitted")
    sp_r.add_argument("--pr-url", required=True)
    sp_r.add_argument("--reviewer", default="replay-reviewer")
    sp_r.add_argument("--state", default="changes_requested",
                      choices=["approved", "changes_requested", "commented"])
    sp_r.add_argument("--body", default=None)

    sp_c = sub.add_parser("comment", help="回放 PR comment")
    sp_c.add_argument("--pr-url", required=True)
    sp_c.add_argument("--commenter", default="replay-commenter")
    sp_c.add_argument("--body", required=True)

    sub.add_parser("ping", help="回放 ping 事件（无 payload 校验）")

    args = parser.parse_args()

    if args.event == "list":
        cmd_list(args.base_url)
        return

    secret = _get_secret(args.secret)
    gh_event_map = {
        "merged": "pull_request", "closed": "pull_request",
        "review": "pull_request_review", "comment": "issue_comment",
        "ping": "ping",
    }
    gh_event = gh_event_map[args.event]

    if args.event in ("merged", "closed"):
        if args.from_github:
            token = os.environ.get("GITHUB_TOKEN")
            pr_block = fetch_real_pr_block(args.pr_url, token)
            # 强制覆盖 action 一致性：merged=True / False 必须配合实际行为
            pr_block["merged"] = (args.event == "merged")
            if args.event == "merged" and not pr_block.get("merge_commit_sha"):
                pr_block["merge_commit_sha"] = args.sha or "replay-sha"
            payload = {
                "action": "closed", "pull_request": pr_block,
                "sender": {"login": args.actor},
                "repository": _repo_block(args.pr_url),
            }
        elif args.event == "merged":
            payload = build_pr_merged_payload(
                args.pr_url, actor=args.actor, sha=args.sha,
            )
        else:
            payload = build_pr_closed_payload(args.pr_url, actor=args.actor)

    elif args.event == "review":
        payload = build_review_payload(
            args.pr_url, reviewer=args.reviewer,
            state=args.state, body=args.body,
        )
    elif args.event == "comment":
        payload = build_comment_payload(
            args.pr_url, commenter=args.commenter, body=args.body,
        )
    elif args.event == "ping":
        payload = build_ping_payload()
    else:
        raise SystemExit(f"未知事件：{args.event}")

    code, resp_body = post_webhook(
        base_url=args.base_url, event=gh_event,
        payload=payload, secret=secret, delivery=args.delivery,
    )
    print(f"HTTP {code}")
    try:
        print(json.dumps(json.loads(resp_body), indent=2, ensure_ascii=False))
    except Exception:
        print(resp_body)

    if not 200 <= code < 300:
        sys.exit(1)


if __name__ == "__main__":
    main()
