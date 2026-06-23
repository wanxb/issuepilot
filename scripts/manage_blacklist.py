"""Blacklist CLI（3.3）。

用法：
    python scripts/manage_blacklist.py list
    python scripts/manage_blacklist.py add repo  "owner/spam"      --reason "已知 ad-stuffer"
    python scripts/manage_blacklist.py add repo  "owner/*"         --reason "owner 暂全屏蔽"
    python scripts/manage_blacklist.py add issue "owner/repo#123"  --reason "重复 issue"
    python scripts/manage_blacklist.py disable <id>
    python scripts/manage_blacklist.py enable  <id>
    python scripts/manage_blacklist.py delete  <id>

容器内：
    docker exec issuepilot-api-1 python /scripts/manage_blacklist.py list
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


async def _cmd_list() -> int:
    from app.db.database import session_scope
    from app.services.blacklist_service import BlacklistService

    async with session_scope() as s:
        rows = await BlacklistService(s).list_all()
    if not rows:
        print("(no blacklist entries)")
        return 0
    print(f"{'ID':<38} {'TYPE':<6} {'EN':<3} {'PATTERN':<35} REASON")
    for r in rows:
        print(
            f"{str(r.id):<38} {r.entity_type:<6} "
            f"{('Y' if r.enabled else 'n'):<3} "
            f"{r.pattern:<35} "
            f"{(r.reason or '-')[:60]}"
        )
    return 0


async def _cmd_add(args: argparse.Namespace) -> int:
    from app.db.database import session_scope
    from app.services.blacklist_service import BlacklistService

    async with session_scope() as s:
        try:
            row = await BlacklistService(s).add(
                entity_type=args.entity_type,
                pattern=args.pattern,
                reason=args.reason,
            )
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
    print(f"ok: added {row.entity_type}={row.pattern} (id={row.id})")
    return 0


async def _cmd_toggle(args: argparse.Namespace, *, enabled: bool) -> int:
    from app.db.database import session_scope
    from app.services.blacklist_service import BlacklistService

    async with session_scope() as s:
        row = await BlacklistService(s).set_enabled(args.id, enabled=enabled)
    if row is None:
        print(f"no entry with id {args.id}", file=sys.stderr)
        return 1
    print(f"ok: {row.id} → enabled={enabled}")
    return 0


async def _cmd_delete(args: argparse.Namespace) -> int:
    from app.db.database import session_scope
    from app.services.blacklist_service import BlacklistService

    async with session_scope() as s:
        ok = await BlacklistService(s).delete(args.id)
    if not ok:
        print(f"no entry with id {args.id}", file=sys.stderr)
        return 1
    print(f"ok: deleted {args.id}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Manage blacklist (3.3).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list all entries")

    a = sub.add_parser("add", help="add a new entry")
    a.add_argument("entity_type", choices=("repo", "issue"))
    a.add_argument("pattern", help="e.g. owner/repo OR owner/* OR owner/repo#42")
    a.add_argument("--reason")

    for cmd, help_ in (("disable", "disable"), ("enable", "enable"), ("delete", "delete")):
        sp = sub.add_parser(cmd, help=help_)
        sp.add_argument("id")

    args = p.parse_args()
    if args.cmd == "list":
        rc = asyncio.run(_cmd_list())
    elif args.cmd == "add":
        rc = asyncio.run(_cmd_add(args))
    elif args.cmd in ("enable", "disable"):
        rc = asyncio.run(_cmd_toggle(args, enabled=(args.cmd == "enable")))
    elif args.cmd == "delete":
        rc = asyncio.run(_cmd_delete(args))
    else:
        rc = 2
    sys.exit(rc)


if __name__ == "__main__":
    main()
