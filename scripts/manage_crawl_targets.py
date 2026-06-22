"""crawl_targets CLI（2.2）—— 增删改查 + 手动触发。

用法：
    python scripts/manage_crawl_targets.py list
    python scripts/manage_crawl_targets.py add \
        --name trending-python-daily \
        --source github_trending \
        --spec '{"language":"python","since":"daily"}' \
        --cron "0 4 * * *"
    python scripts/manage_crawl_targets.py add \
        --name explicit-favs \
        --source explicit_repos \
        --spec '{"repos":["psf/requests","pallets/flask"]}' \
        --cron "30 5 * * *"
    python scripts/manage_crawl_targets.py disable <name|id>
    python scripts/manage_crawl_targets.py enable  <name|id>
    python scripts/manage_crawl_targets.py delete  <name|id>
    python scripts/manage_crawl_targets.py run-now <name|id>

backend 容器内执行（依赖 SQLAlchemy + .env）：
    docker exec issuepilot-api-1 python scripts/manage_crawl_targets.py ...
也可以本机有 venv 时直接跑（需 DATABASE_URL 指向 localhost:5432）。

CLI 不直接修 scheduler；让 scheduler.reload_crawl_target / unregister 经
API 端点统一管理（本 CLI 修 DB 后建议 restart api 或 hit /admin/scheduler-reload）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

# 让脚本无论从 host 还是从容器内都能 import app.*：
#   host：脚本在 <repo>/scripts/，加 <repo>/backend
#   容器：脚本在 /scripts/，app 已在 /app（PYTHONPATH 由 uvicorn 设置）
for _candidate in (
    Path(__file__).resolve().parent.parent / "backend",  # host 布局
    Path("/app"),                                        # 容器布局
):
    if _candidate.exists() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))


async def _cmd_list() -> int:
    from app.db.database import session_scope
    from app.services.crawl_target_service import CrawlTargetService

    async with session_scope() as s:
        targets = await CrawlTargetService(s).list_all()

    if not targets:
        print("(no crawl_targets configured)")
        return 0
    print(f"{'NAME':<28} {'SOURCE':<16} {'CRON':<14} {'EN':<3} {'LAST_STATUS':<11} LAST_RUN")
    for t in targets:
        print(
            f"{t.name:<28} {t.source:<16} {t.cron:<14} "
            f"{('Y' if t.enabled else 'n'):<3} "
            f"{(t.last_status or '-'):<11} "
            f"{t.last_run_at.isoformat() if t.last_run_at else '-'}"
        )
    return 0


async def _cmd_add(args: argparse.Namespace) -> int:
    from app.db.database import session_scope
    from app.services.crawl_target_service import (
        CrawlTargetInvalid, CrawlTargetService,
    )

    try:
        spec = json.loads(args.spec)
    except json.JSONDecodeError as e:
        print(f"ERROR: --spec must be valid JSON: {e}", file=sys.stderr)
        return 2
    if not isinstance(spec, dict):
        print("ERROR: --spec must be a JSON object", file=sys.stderr)
        return 2

    async with session_scope() as s:
        svc = CrawlTargetService(s)
        try:
            target = await svc.create(
                name=args.name, source=args.source, spec=spec,
                cron=args.cron, enabled=not args.disabled,
            )
        except CrawlTargetInvalid as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
    print(f"ok: created {target.name} (id={target.id})")
    print("Restart api / hit POST /api/v1/admin/scheduler-reload to register.")
    return 0


async def _cmd_toggle(name_or_id: str, *, enabled: bool) -> int:
    from app.db.database import session_scope
    from app.services.crawl_target_service import (
        CrawlTargetNotFound, CrawlTargetService,
    )

    async with session_scope() as s:
        svc = CrawlTargetService(s)
        target = await _resolve(svc, name_or_id)
        if target is None:
            return 1
        await svc.set_enabled(target.id, enabled=enabled)
    print(f"ok: {target.name} → enabled={enabled}")
    return 0


async def _cmd_delete(name_or_id: str) -> int:
    from app.db.database import session_scope
    from app.services.crawl_target_service import CrawlTargetService

    async with session_scope() as s:
        svc = CrawlTargetService(s)
        target = await _resolve(svc, name_or_id)
        if target is None:
            return 1
        await svc.delete(target.id)
    print(f"ok: deleted {target.name}")
    return 0


async def _cmd_run_now(name_or_id: str) -> int:
    """直接 send_task 到 worker，不等结果。"""
    from app.db.database import session_scope
    from app.services.crawl_target_service import CrawlTargetService

    async with session_scope() as s:
        svc = CrawlTargetService(s)
        target = await _resolve(svc, name_or_id)
        if target is None:
            return 1
        tid = str(target.id)

    from app.workers.celery_app import celery_app
    result = celery_app.send_task(
        "app.workers.scheduled_crawl_worker.run_target",
        args=[tid], queue="analyze_queue",
    )
    print(f"ok: dispatched task_id={result.id} (target={tid})")
    return 0


async def _resolve(svc: Any, name_or_id: str) -> Any:
    from app.services.crawl_target_service import CrawlTargetNotFound

    try:
        tid = uuid.UUID(name_or_id)
        try:
            return await svc.get(tid)
        except CrawlTargetNotFound:
            pass
    except ValueError:
        pass
    try:
        return await svc.get_by_name(name_or_id)
    except CrawlTargetNotFound:
        print(f"ERROR: no target with name/id {name_or_id!r}", file=sys.stderr)
        return None


def main() -> None:
    p = argparse.ArgumentParser(description="Manage crawl_targets (2.2).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list all crawl_targets")
    a = sub.add_parser("add", help="create a new crawl_target")
    a.add_argument("--name", required=True)
    a.add_argument("--source", required=True,
                   choices=("github_trending", "explicit_repos"))
    a.add_argument("--spec", required=True,
                   help='JSON spec, e.g. \'{"language":"python","since":"daily"}\'')
    a.add_argument("--cron", required=True,
                   help='5-field cron expression in UTC, e.g. "0 4 * * *"')
    a.add_argument("--disabled", action="store_true",
                   help="create as disabled")
    for name, help_ in (
        ("disable", "disable a target"),
        ("enable", "enable a target"),
        ("delete", "delete a target"),
        ("run-now", "dispatch a target run immediately (Celery)"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("ref", help="target name or UUID")

    args = p.parse_args()
    if args.cmd == "list":
        rc = asyncio.run(_cmd_list())
    elif args.cmd == "add":
        rc = asyncio.run(_cmd_add(args))
    elif args.cmd == "disable":
        rc = asyncio.run(_cmd_toggle(args.ref, enabled=False))
    elif args.cmd == "enable":
        rc = asyncio.run(_cmd_toggle(args.ref, enabled=True))
    elif args.cmd == "delete":
        rc = asyncio.run(_cmd_delete(args.ref))
    elif args.cmd == "run-now":
        rc = asyncio.run(_cmd_run_now(args.ref))
    else:
        rc = 2
    sys.exit(rc)


if __name__ == "__main__":
    main()
