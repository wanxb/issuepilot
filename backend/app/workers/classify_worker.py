"""classify_worker — RejectionClassifier Celery 任务（2.3）。

入参：reason_id（rejection_reasons.id）

流程：
    1. 加载 RejectionReason 行；若 classified_by 非空说明已分类，跳过（幂等）
    2. 拉相关上下文（PR / Issue / DevTask 用于消歧）
    3. 调 RejectionClassifier harness
    4. UPDATE rejection_reasons 字段 + classified_by="rejection_classifier" +
       classifier_metadata={model, prompt_version, llm_attempts}
    5. 写 llm_call_logs

异常：
    - ToolNotCalledError / SchemaValidationError：classified_by=
      "rejection_classifier_failed" + classifier_metadata 存错误信息，
      不重试（人工介入或后续手动重分类）
    - LLMCallError：Celery autoretry 1 次；仍失败 → 同上
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from celery import Task
from sqlalchemy.orm import selectinload

from app.agents.rejection_classifier import (
    RejectionClassifier,
    SchemaValidationError,
    ToolNotCalledError,
)
from app.agents.schemas import RejectionClassifierInput
from app.db.database import session_scope
from app.llm.base import LLMCallError
from app.llm.factory import build_client
from app.llm.logging import persist_attempts
from app.models.dev_task import DevTask
from app.models.enums import (
    AgentBAttribution,
    AgentKind,
    RejectionCategory,
    RejectionDimension,
    RejectionSeverity,
    RejectionSource,
)
from app.models.pull_request import PullRequest
from app.models.rejection_reason import RejectionReason
from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)


@celery_app.task(
    name="app.workers.classify_worker.classify_rejection",
    bind=True,
    autoretry_for=(LLMCallError,),
    max_retries=1,
    default_retry_delay=10,
    acks_late=True,
)
def classify_rejection(self: Task, reason_id: str) -> dict[str, object]:
    return asyncio.run(_classify_async(uuid.UUID(reason_id)))


async def _classify_async(reason_id: uuid.UUID) -> dict[str, object]:
    client = build_client("rejection_classifier")
    try:
        # ---- Phase 1: 加载 + 幂等检查 + 构造 input ----
        async with session_scope() as s:
            reason = await s.get(
                RejectionReason, reason_id,
                options=[
                    selectinload(RejectionReason.pull_request),
                    selectinload(RejectionReason.issue),
                ],
            )
            if reason is None:
                log.error("classify_worker.reason_missing", reason_id=str(reason_id))
                return {"error": "reason_not_found"}

            if reason.classified_by:
                log.info(
                    "classify_worker.already_classified",
                    reason_id=str(reason_id),
                    classified_by=reason.classified_by,
                )
                return {"skipped": True, "classified_by": reason.classified_by}

            # source=agent_c 的本就是 Agent C 自己分好的；不重复分类
            if reason.source == RejectionSource.AGENT_C:
                log.info(
                    "classify_worker.skip_agent_c",
                    reason_id=str(reason_id),
                )
                return {"skipped": True, "reason": "agent_c_source"}

            input_data = await _build_input(s, reason)
            agent_input = RejectionClassifierInput(**input_data)
            issue_id = reason.issue_id

        # ---- Phase 2: 调 LLM（不持 DB 锁）----
        agent = RejectionClassifier(client)
        try:
            output, llm_resp = await agent.classify(agent_input)
        except (ToolNotCalledError, SchemaValidationError) as e:
            await _mark_failed(reason_id, reason=type(e).__name__, detail=str(e)[:500])
            log.error(
                "classify_worker.model_output_invalid",
                reason_id=str(reason_id),
                error=str(e)[:300],
            )
            return {"error": "model_output_invalid"}

        # ---- Phase 3: UPDATE rejection_reasons + 写 llm_call_logs ----
        async with session_scope() as s:
            reason = await s.get(RejectionReason, reason_id)
            if reason is None:
                log.error("classify_worker.reason_lost_after_llm", reason_id=str(reason_id))
                return {"error": "reason_lost"}

            reason.category = RejectionCategory(output.category)
            reason.severity = RejectionSeverity(output.severity)
            reason.dimension = (
                RejectionDimension(output.dimension) if output.dimension else None
            )
            reason.agent_b_attribution = AgentBAttribution(output.agent_b_attribution)
            reason.classified_by = "rejection_classifier"
            reason.classifier_metadata = {
                "model": llm_resp.model,
                "provider": llm_resp.provider,
                "is_fallback": llm_resp.is_fallback,
                "prompt_version": agent.prompt_version,
                "classified_reason": output.classified_reason,
                "cost_usd": sum(a.cost_usd for a in llm_resp.all_attempts),
            }
            if not reason.detail.startswith("[classified]"):
                reason.detail = (
                    f"[classified] {output.classified_reason} | original: {reason.detail}"
                )[:2000]

            await persist_attempts(
                s,
                llm_resp.all_attempts,
                agent_kind=AgentKind.REJECTION_CLASSIFIER,
                issue_id=issue_id,
                prompt_version=agent.prompt_version,
            )

        log.info(
            "classify_worker.done",
            reason_id=str(reason_id),
            category=output.category,
            severity=output.severity,
            attribution=output.agent_b_attribution,
            cost=sum(a.cost_usd for a in llm_resp.all_attempts),
        )
        return {
            "category": output.category,
            "severity": output.severity,
            "dimension": output.dimension,
            "agent_b_attribution": output.agent_b_attribution,
        }

    finally:
        await client.aclose()


async def _build_input(session, reason: RejectionReason) -> dict[str, Any]:
    """从 RejectionReason 衍生上下文。可选字段缺失就空。"""
    pr = reason.pull_request
    issue = reason.issue

    diff_summary: str | None = None
    files_changed: list[str] = []
    agent_c_verdict: str | None = None

    if pr is not None and pr.review_task_id is not None:
        # 通过 review_task 找到对应 dev_task（同 issue，最近一次成功）
        # 简化：直接拿 PR 关联的最近一次 dev_task
        from sqlalchemy import select
        from app.models.review_task import ReviewTask

        rt = await session.get(ReviewTask, pr.review_task_id)
        if rt is not None and rt.verdict is not None:
            agent_c_verdict = rt.verdict.value
            if rt.dev_task_id is not None:
                dt = await session.get(DevTask, rt.dev_task_id)
                if dt is not None:
                    diff_summary = dt.diff_summary
                    files_changed = list(dt.files_changed or [])

    return {
        "raw_text": reason.raw_text or reason.detail,
        "source": reason.source.value,
        "pr_title": pr.title if pr else None,
        "pr_body_excerpt": (pr.body or "")[:1200] if pr else None,
        "diff_summary": diff_summary,
        "files_changed": files_changed,
        "issue_title": issue.title if issue else None,
        "agent_c_verdict": agent_c_verdict,
    }


async def _mark_failed(reason_id: uuid.UUID, *, reason: str, detail: str) -> None:
    async with session_scope() as s:
        r = await s.get(RejectionReason, reason_id)
        if r is None:
            return
        r.classified_by = "rejection_classifier_failed"
        r.classifier_metadata = {
            "error": reason,
            "detail": detail,
        }
