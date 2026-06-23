"""eval_samples CLI（3.1）—— Golden Set 标注管理。

用法：
    python scripts/manage_eval_samples.py list
    python scripts/manage_eval_samples.py add <issue_id_or_url> \
        --kind positive_merged_clean --label "snake_case-prefer" \
        [--source dry_run_phase2] [--notes "评分 8.15 / merged_clean"]
    python scripts/manage_eval_samples.py auto-pick \
        --kind negative_agent_b_fault   # 从 rejection_reasons 自动挑
    python scripts/manage_eval_samples.py remove <sample_id_or_label>

sample_kind 取值见 backend/app/models/eval_sample.py docstring。

容器内执行：
    docker exec issuepilot-api-1 python /scripts/manage_eval_samples.py list
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from pathlib import Path
from typing import Any

for _c in (
    Path(__file__).resolve().parent.parent / "backend",
    Path("/app"),
):
    if _c.exists() and str(_c) not in sys.path:
        sys.path.insert(0, str(_c))


def _resolve_issue(text: str) -> str | None:
    """text 可能是 UUID / GitHub URL / "owner/repo#N"，返回 UUID 字符串。"""
    try:
        uuid.UUID(text)
        return text
    except ValueError:
        pass
    return None  # URL / shortcode 解析在 async resolver 里做


async def _resolve_async(s: Any, raw: str) -> uuid.UUID | None:
    from sqlalchemy import select
    from app.models.issue import Issue

    direct = _resolve_issue(raw)
    if direct:
        return uuid.UUID(direct)

    # github URL 形如 https://github.com/owner/repo/issues/N
    m = re.match(
        r"^https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)", raw,
    )
    if not m:
        return None
    owner, repo_name, num = m.group(1), m.group(2), int(m.group(3))
    full = f"{owner}/{repo_name}"

    from app.models.repository import Repository

    stmt = (
        select(Issue.id)
        .join(Repository, Repository.id == Issue.repository_id)
        .where(Repository.full_name == full)
        .where(Issue.github_number == num)
    )
    row = (await s.execute(stmt)).scalar_one_or_none()
    return row


async def _cmd_list() -> int:
    from app.db.database import session_scope
    from app.models.eval_sample import EvalSample
    from sqlalchemy import select

    async with session_scope() as s:
        rows = (await s.execute(
            select(EvalSample).order_by(EvalSample.created_at.desc())
        )).scalars().all()

    if not rows:
        print("(no eval_samples yet)")
        return 0
    print(f"{'KIND':<28} {'LABEL':<30} {'SOURCE':<15} ISSUE_ID")
    for r in rows:
        print(f"{r.sample_kind:<28} {r.label:<30} {(r.source or '-'):<15} {r.issue_id}")
    return 0


async def _cmd_add(args: argparse.Namespace) -> int:
    from app.db.database import session_scope
    from app.models.eval_sample import EvalSample

    async with session_scope() as s:
        issue_id = await _resolve_async(s, args.issue)
        if issue_id is None:
            print(f"ERROR: cannot resolve {args.issue!r} to an issue_id",
                  file=sys.stderr)
            return 2
        sample = EvalSample(
            issue_id=issue_id,
            sample_kind=args.kind,
            label=args.label,
            source=args.source,
            notes=args.notes,
        )
        s.add(sample)
        await s.flush()
        print(f"ok: added {sample.sample_kind}/{sample.label} (id={sample.id})")
    return 0


async def _cmd_auto_pick(args: argparse.Namespace) -> int:
    """从 rejection_reasons / pull_requests 自动挑符合条件的样本。"""
    from app.db.database import session_scope
    from app.models.eval_sample import EvalSample
    from app.models.rejection_reason import RejectionReason
    from app.models.pull_request import PullRequest
    from app.models.enums import (
        AgentBAttribution, PRFinalOutcome, RejectionCategory, RejectionSeverity,
    )
    from sqlalchemy import select

    async with session_scope() as s:
        if args.kind == "negative_agent_b_fault":
            stmt = (
                select(RejectionReason)
                .where(RejectionReason.agent_b_attribution == AgentBAttribution.YES)
                .where(RejectionReason.severity == RejectionSeverity.BLOCKER)
            )
            rows = (await s.execute(stmt)).scalars().all()
            added = 0
            for r in rows:
                label = f"{r.category.value}-{r.dimension.value if r.dimension else 'na'}"
                # 跳过已标注的
                dup = (await s.execute(
                    select(EvalSample)
                    .where(EvalSample.issue_id == r.issue_id)
                    .where(EvalSample.sample_kind == args.kind)
                )).scalar_one_or_none()
                if dup is not None:
                    continue
                s.add(EvalSample(
                    issue_id=r.issue_id, sample_kind=args.kind,
                    label=label, source="auto_pick_rejection",
                    notes=(r.detail or "")[:300],
                ))
                added += 1
            print(f"ok: auto-picked {added} negative_agent_b_fault samples")
            return 0

        if args.kind == "positive_merged_clean":
            stmt = (
                select(PullRequest)
                .where(PullRequest.final_outcome == PRFinalOutcome.MERGED_CLEAN)
            )
            rows = (await s.execute(stmt)).scalars().all()
            added = 0
            for pr in rows:
                dup = (await s.execute(
                    select(EvalSample)
                    .where(EvalSample.issue_id == pr.issue_id)
                    .where(EvalSample.sample_kind == args.kind)
                )).scalar_one_or_none()
                if dup is not None:
                    continue
                s.add(EvalSample(
                    issue_id=pr.issue_id, sample_kind=args.kind,
                    label="merged_clean",
                    source="auto_pick_pr",
                    notes=f"PR #{pr.github_pr_number} merged by {pr.merger_login}",
                ))
                added += 1
            print(f"ok: auto-picked {added} positive_merged_clean samples")
            return 0

        if args.kind == "style_pattern":
            stmt = (
                select(RejectionReason)
                .where(RejectionReason.category == RejectionCategory.STYLE_MISMATCH)
            )
            rows = (await s.execute(stmt)).scalars().all()
            added = 0
            for r in rows:
                dup = (await s.execute(
                    select(EvalSample)
                    .where(EvalSample.issue_id == r.issue_id)
                    .where(EvalSample.sample_kind == args.kind)
                )).scalar_one_or_none()
                if dup is not None:
                    continue
                s.add(EvalSample(
                    issue_id=r.issue_id, sample_kind=args.kind,
                    label="style_mismatch",
                    source="auto_pick_rejection",
                    notes=(r.detail or "")[:300],
                ))
                added += 1
            print(f"ok: auto-picked {added} style_pattern samples")
            return 0

        print(f"ERROR: --kind {args.kind!r} not supported in auto-pick",
              file=sys.stderr)
        return 2


async def _cmd_remove(ident: str) -> int:
    from app.db.database import session_scope
    from app.models.eval_sample import EvalSample
    from sqlalchemy import select

    async with session_scope() as s:
        try:
            sid = uuid.UUID(ident)
            row = await s.get(EvalSample, sid)
            if row is None:
                print(f"no sample with id {sid}", file=sys.stderr)
                return 1
            await s.delete(row)
            print(f"ok: removed {sid}")
            return 0
        except ValueError:
            pass
        # 按 label 删除（删一批）
        stmt = select(EvalSample).where(EvalSample.label == ident)
        rows = (await s.execute(stmt)).scalars().all()
        if not rows:
            print(f"no sample with label {ident!r}", file=sys.stderr)
            return 1
        for r in rows:
            await s.delete(r)
        print(f"ok: removed {len(rows)} samples with label={ident!r}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Manage eval_samples (3.1 Golden Set).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list all samples")

    a = sub.add_parser("add", help="add a sample by issue_id or URL")
    a.add_argument("issue", help="UUID 或 GitHub issue URL")
    a.add_argument("--kind", required=True,
                   choices=("positive_merged_clean", "negative_agent_b_fault",
                            "style_pattern", "tricky_evaluation"))
    a.add_argument("--label", required=True)
    a.add_argument("--source")
    a.add_argument("--notes")

    ap = sub.add_parser("auto-pick", help="pick from existing DB rows")
    ap.add_argument("--kind", required=True,
                    choices=("positive_merged_clean", "negative_agent_b_fault",
                             "style_pattern"))

    r = sub.add_parser("remove", help="remove by id or label")
    r.add_argument("ident")

    args = p.parse_args()
    if args.cmd == "list":
        rc = asyncio.run(_cmd_list())
    elif args.cmd == "add":
        rc = asyncio.run(_cmd_add(args))
    elif args.cmd == "auto-pick":
        rc = asyncio.run(_cmd_auto_pick(args))
    elif args.cmd == "remove":
        rc = asyncio.run(_cmd_remove(args.ident))
    else:
        rc = 2
    sys.exit(rc)


if __name__ == "__main__":
    main()
