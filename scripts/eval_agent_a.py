"""Agent A 离线评估框架（3.1）。

从 eval_samples（kind=positive_merged_clean / tricky_evaluation / 任意）
拉样本 → 用当前 Agent A prompt 重跑评估 → 与历史 evaluation 对比。

用法：
    # 全量重跑（小心：每个样本一次真实 LLM 调用）
    python scripts/eval_agent_a.py run --limit 5

    # 只看某 kind
    python scripts/eval_agent_a.py run --kind positive_merged_clean --limit 10

    # 干跑（不调 LLM，只显示样本 + 当前 evaluation）
    python scripts/eval_agent_a.py run --dry-run

输出：JSON 行流到 stdout（每个样本一行），方便 jq 或 import 工具消费。
每行包含：
    {
      "sample_id": ..., "issue_id": ..., "sample_kind": ..., "label": ...,
      "title": ..., "url": ...,
      "old_score": float, "new_score": float | null, "delta": float | null,
      "old_recommend": bool, "new_recommend": bool | null,
      "agreement": "match" | "diverge_pos" | "diverge_neg" | "no_old"
    }
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

for _c in (
    Path(__file__).resolve().parent.parent / "backend",
    Path("/app"),
):
    if _c.exists() and str(_c) not in sys.path:
        sys.path.insert(0, str(_c))


async def _build_agent_a_input(s: Any, issue: Any) -> Any:
    """从 DB 还原 Agent A 输入。简化：只读必要字段。"""
    from app.agents.schemas import AgentAInput
    from app.models.repository import Repository

    repo = await s.get(Repository, issue.repository_id)
    return AgentAInput(
        issue_title=issue.title,
        issue_body=issue.body or "",
        issue_labels=list(issue.labels or []),
        issue_url=issue.github_url,
        repo_full_name=repo.full_name,
        repo_description=repo.description,
        repo_language=repo.primary_language,
        repo_stars=repo.stars,
        repo_topics=list(repo.topics or []),
        repo_last_commit_days=0,           # 历史值已丢，用 0；不影响相对比较
        repo_open_prs_count=repo.open_prs_count,
        repo_merged_prs_last_30d=repo.merged_prs_last_30d,
    )


async def run(args: argparse.Namespace) -> int:
    from app.db.database import session_scope
    from app.models.eval_sample import EvalSample
    from app.models.evaluation import Evaluation
    from app.models.issue import Issue
    from sqlalchemy import select

    # 1) 拉样本（带 issue + evaluation）
    async with session_scope() as s:
        stmt = (
            select(EvalSample, Issue, Evaluation)
            .join(Issue, Issue.id == EvalSample.issue_id)
            .join(Evaluation, Evaluation.issue_id == Issue.id, isouter=True)
            .order_by(EvalSample.created_at)
        )
        if args.kind:
            stmt = stmt.where(EvalSample.sample_kind == args.kind)
        if args.limit:
            stmt = stmt.limit(args.limit)
        rows = (await s.execute(stmt)).all()

        # 提取需要的字段，避免 session 关闭后访问被惰性加载
        prepared: list[dict[str, Any]] = []
        for sample, issue, evaluation in rows:
            ai = await _build_agent_a_input(s, issue)
            prepared.append({
                "sample_id": str(sample.id),
                "issue_id": str(issue.id),
                "sample_kind": sample.sample_kind,
                "label": sample.label,
                "title": issue.title,
                "url": issue.github_url,
                "agent_input": ai,
                "old_score": evaluation.total_score if evaluation else None,
                "old_recommend": evaluation.is_worth_developing if evaluation else None,
            })

    if not prepared:
        print(json.dumps({"warn": "no samples matched"}), file=sys.stderr)
        return 0

    # 2) 对每个样本调 Agent A（dry-run 跳过）
    if args.dry_run:
        for p in prepared:
            print(json.dumps({
                "sample_id": p["sample_id"], "issue_id": p["issue_id"],
                "sample_kind": p["sample_kind"], "label": p["label"],
                "title": p["title"], "url": p["url"],
                "old_score": p["old_score"],
                "old_recommend": p["old_recommend"],
                "new_score": None, "delta": None,
                "new_recommend": None, "agreement": "dry_run",
            }, ensure_ascii=False))
        return 0

    from app.agents.agent_a import AgentA
    from app.llm.factory import build_client

    client = build_client("agent_a")
    try:
        agent = AgentA(client)
        for p in prepared:
            try:
                output, _resp = await agent.analyze(p["agent_input"])
                new_score = output.total_score
                new_recommend = output.is_worth_developing
                if p["old_score"] is None:
                    agreement = "no_old"
                elif new_recommend == p["old_recommend"]:
                    agreement = "match"
                else:
                    agreement = (
                        "diverge_pos" if new_recommend else "diverge_neg"
                    )
                delta = (
                    new_score - p["old_score"]
                    if p["old_score"] is not None else None
                )
            except Exception as e:
                print(json.dumps({
                    "sample_id": p["sample_id"], "error": str(e)[:300],
                }, ensure_ascii=False), file=sys.stderr)
                continue

            print(json.dumps({
                "sample_id": p["sample_id"], "issue_id": p["issue_id"],
                "sample_kind": p["sample_kind"], "label": p["label"],
                "title": p["title"], "url": p["url"],
                "old_score": p["old_score"], "old_recommend": p["old_recommend"],
                "new_score": new_score, "delta": delta,
                "new_recommend": new_recommend,
                "agreement": agreement,
            }, ensure_ascii=False))
    finally:
        await client.aclose()
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Agent A offline eval (3.1)")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="rerun Agent A on eval_samples")
    r.add_argument("--kind",
                   choices=("positive_merged_clean", "negative_agent_b_fault",
                            "style_pattern", "tricky_evaluation"))
    r.add_argument("--limit", type=int, default=None)
    r.add_argument("--dry-run", action="store_true",
                   help="don't call LLM, just print samples + old scores")
    args = p.parse_args()
    if args.cmd == "run":
        sys.exit(asyncio.run(run(args)))
    sys.exit(2)


if __name__ == "__main__":
    main()
