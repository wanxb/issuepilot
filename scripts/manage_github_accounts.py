"""GitHub 账号池 CLI（3.3）。

用法：
    python scripts/manage_github_accounts.py list
    python scripts/manage_github_accounts.py add \
        --name dev-bot-1 --role dev --token ghp_xxx --username dev-bot-1
    python scripts/manage_github_accounts.py pick --role dev --sticky owner/repo
    python scripts/manage_github_accounts.py disable <id>
    python scripts/manage_github_accounts.py delete <id>
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

for _c in (
    Path(__file__).resolve().parent.parent / "backend",
    Path("/app"),
):
    if _c.exists() and str(_c) not in sys.path:
        sys.path.insert(0, str(_c))


def _mask(token: str) -> str:
    if not token:
        return "(empty)"
    if len(token) <= 8:
        return token[:2] + "***"
    return token[:4] + "..." + token[-4:]


async def _cmd_list() -> int:
    from app.db.database import session_scope
    from app.services.github_account_service import GitHubAccountService

    async with session_scope() as s:
        rows = await GitHubAccountService(s).list_all()
    if not rows:
        print("(no github_accounts)")
        return 0
    print(f"{'ID':<38} {'NAME':<20} {'ROLE':<10} {'EN':<3} {'USER':<20} {'TOKEN':<14} LAST_USED")
    for r in rows:
        print(
            f"{str(r.id):<38} {r.name:<20} {r.role:<10} "
            f"{('Y' if r.enabled else 'n'):<3} "
            f"{(r.username or '-'):<20} "
            f"{_mask(r.token):<14} "
            f"{r.last_used_at.isoformat() if r.last_used_at else '-'}"
        )
    return 0


async def _cmd_add(args: argparse.Namespace) -> int:
    from app.db.database import session_scope
    from app.services.github_account_service import (
        GitHubAccountError, GitHubAccountService,
    )

    async with session_scope() as s:
        try:
            row = await GitHubAccountService(s).add(
                name=args.name, role=args.role, token=args.token,
                username=args.username, notes=args.notes,
            )
        except GitHubAccountError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
    print(f"ok: added {row.name} (id={row.id})")
    return 0


async def _cmd_pick(args: argparse.Namespace) -> int:
    from app.db.database import session_scope
    from app.services.github_account_service import GitHubAccountService

    async with session_scope() as s:
        row = await GitHubAccountService(s).pick(
            role=args.role, sticky_key=args.sticky,
        )
    if row is None:
        print(f"(no enabled account for role={args.role})")
        return 1
    print(f"ok: picked {row.name} (token={_mask(row.token)}, user={row.username})")
    return 0


async def _cmd_toggle(args: argparse.Namespace, *, enabled: bool) -> int:
    from app.db.database import session_scope
    from app.services.github_account_service import GitHubAccountService

    async with session_scope() as s:
        row = await GitHubAccountService(s).set_enabled(args.id, enabled=enabled)
    if row is None:
        print(f"no entry with id {args.id}", file=sys.stderr)
        return 1
    print(f"ok: {row.name} → enabled={enabled}")
    return 0


async def _cmd_delete(args: argparse.Namespace) -> int:
    from app.db.database import session_scope
    from app.services.github_account_service import GitHubAccountService

    async with session_scope() as s:
        ok = await GitHubAccountService(s).delete(args.id)
    if not ok:
        print(f"no entry with id {args.id}", file=sys.stderr)
        return 1
    print(f"ok: deleted {args.id}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Manage github_accounts (3.3)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list all")

    a = sub.add_parser("add", help="add account")
    a.add_argument("--name", required=True)
    a.add_argument("--role", required=True,
                   choices=("crawler", "dev", "webhook"))
    a.add_argument("--token", required=True)
    a.add_argument("--username")
    a.add_argument("--notes")

    pi = sub.add_parser("pick", help="dry-pick an account")
    pi.add_argument("--role", required=True,
                    choices=("crawler", "dev", "webhook"))
    pi.add_argument("--sticky", help="sticky key (e.g. owner/repo)")

    for cmd, help_ in (("disable", "disable"), ("enable", "enable"), ("delete", "delete")):
        sp = sub.add_parser(cmd, help=help_)
        sp.add_argument("id")

    args = p.parse_args()
    if args.cmd == "list":
        rc = asyncio.run(_cmd_list())
    elif args.cmd == "add":
        rc = asyncio.run(_cmd_add(args))
    elif args.cmd == "pick":
        rc = asyncio.run(_cmd_pick(args))
    elif args.cmd in ("enable", "disable"):
        rc = asyncio.run(_cmd_toggle(args, enabled=(args.cmd == "enable")))
    elif args.cmd == "delete":
        rc = asyncio.run(_cmd_delete(args))
    else:
        rc = 2
    sys.exit(rc)


if __name__ == "__main__":
    main()
