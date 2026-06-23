"""3.3 周报导出 CLI —— 落盘 reports/weekly-YYYY-MM-DD.md。

用法：
    python scripts/export_weekly_report.py [--days 7] [--out reports/]
    python scripts/export_weekly_report.py --stdout    # 直接打到 stdout
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

for _c in (
    Path(__file__).resolve().parent.parent / "backend",
    Path("/app"),
):
    if _c.exists() and str(_c) not in sys.path:
        sys.path.insert(0, str(_c))


async def run(args: argparse.Namespace) -> int:
    # 直接调内部 weekly_report，避免起 HTTP 服务
    from app.api.dashboard import weekly_report
    from app.db.database import session_scope
    from app.services.report_export import render_weekly_report_markdown

    async with session_scope() as s:
        payload = await weekly_report(days=args.days, session=s)

    md = render_weekly_report_markdown(payload)
    if args.stdout:
        sys.stdout.write(md)
        return 0

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"weekly-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.md"
    fpath = out_dir / fname
    fpath.write_text(md, encoding="utf-8")
    print(f"ok: wrote {fpath} ({len(md)} bytes)")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Export weekly report as Markdown (3.3)")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--out", default="reports",
                   help="output directory（默认 reports/）")
    p.add_argument("--stdout", action="store_true",
                   help="print to stdout instead of writing a file")
    args = p.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
