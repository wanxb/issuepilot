"""review_worker — Agent C Celery 任务（里程碑 1.5b）。

输入：issue_id + dev_task_id（由 dev_worker 成功后发出）。

流程：
    Phase 1：load Issue + DevTask + Repository（含 RepoProfile，1.5c 注入；
             1.5b 期间 profile 为 None 时降级为通用规则）
    Phase 2：状态 QUEUED_REVIEW → IN_REVIEW；创建 ReviewTask(RUNNING)
    Phase 3：构造 AgentCInput → AgentC.review()
    Phase 4：APPROVED 路径
                 - 写 review_task 字段（verdict, dimensions, pr_title/body, score）
                 - 状态保持 IN_REVIEW（PR 创建留给 1.5d）
             REJECTED 路径
                 - 写 review_task 字段（rejection_reason）
                 - 写 rejection_reasons 一条（source=agent_c）
                 - 状态 IN_REVIEW → REVIEW_REJECTED

异常：
    - ToolNotCalledError / SchemaValidationError：review_task FAILED，
      issue 转 REVIEW_REJECTED（带合成原因）
    - LLMCallError：Celery autoretry 1 次；仍失败则同上
    - InvalidTransitionError：日志记录，不重试
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import structlog
from celery import Task
from sqlalchemy import select

from app.agents.agent_c import (
    AgentC,
    SchemaValidationError,
    ToolNotCalledError,
)
from app.agents.schemas import AgentCInput, AgentCOutput, TestResult
from app.db.database import session_scope
from app.llm.base import LLMCallError
from app.llm.factory import build_client
from app.llm.logging import persist_attempts
from app.models.dev_task import DevTask
from app.models.enums import (
    AgentBAttribution,
    AgentKind,
    IssueStatus,
    RejectionCategory,
    RejectionDimension,
    RejectionSeverity,
    RejectionSource,
    ReviewTaskStatus,
    ReviewVerdict,
)
from app.models.issue import Issue
from app.models.rejection_reason import RejectionReason
from app.models.review_task import ReviewTask
from app.services.issue_service import (
    InvalidTransitionError,
    IssueNotFoundError,
    IssueService,
)
from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)


@celery_app.task(
    name="app.workers.review_worker.review_dev_task",
    bind=True,
    autoretry_for=(LLMCallError,),
    max_retries=1,
    default_retry_delay=10,
    acks_late=True,
)
def review_dev_task(self: Task, issue_id: str, dev_task_id: str) -> dict[str, object]:
    """Celery 同步包装：内部跑 asyncio.run。"""
    return asyncio.run(
        _review_dev_task_async(uuid.UUID(issue_id), uuid.UUID(dev_task_id))
    )


# ---------------------------------------------------------------------------


async def _review_dev_task_async(
    issue_id: uuid.UUID,
    dev_task_id: uuid.UUID,
) -> dict[str, object]:
    client = build_client("agent_c")
    review_task_id: uuid.UUID | None = None
    try:
        agent = AgentC(client)

        # ---- Phase 1+2: load + 创建 review_task + 转 IN_REVIEW ----
        async with session_scope() as s:
            svc = IssueService(s)
            try:
                issue = await svc.get(issue_id)
            except IssueNotFoundError:
                log.error("review_worker.issue_missing", issue_id=str(issue_id))
                return {"error": "issue_not_found"}

            if issue.status != IssueStatus.QUEUED_REVIEW:
                log.info(
                    "review_worker.skip_not_queued",
                    issue_id=str(issue_id),
                    status=issue.status.value,
                )
                return {"skipped": True, "status": issue.status.value}

            dev_task = await s.get(DevTask, dev_task_id)
            if dev_task is None:
                log.error("review_worker.dev_task_missing", dev_task_id=str(dev_task_id))
                return {"error": "dev_task_not_found"}

            # 计算 attempt_number：同 issue 已有 review_task 数 + 1
            count_stmt = select(ReviewTask).where(ReviewTask.issue_id == issue.id)
            prior_count = len((await s.execute(count_stmt)).scalars().all())

            review_task = ReviewTask(
                issue_id=issue.id,
                dev_task_id=dev_task.id,
                attempt_number=prior_count + 1,
                status=ReviewTaskStatus.RUNNING,
                prompt_version=agent.prompt_version,
                started_at=datetime.now(timezone.utc),
            )
            s.add(review_task)
            await s.flush()
            review_task_id = review_task.id

            await svc.mark_in_review(issue)

            # 准备 AgentCInput 的源数据
            repo = issue.repository
            agent_input_kwargs = {
                "issue_title": issue.title,
                "issue_body": _truncate_body(issue.body),
                "repo_full_name": repo.full_name,
                "repo_language": repo.primary_language,
                "diff_content": _prepare_diff(dev_task.git_diff),
                "diff_summary": dev_task.diff_summary or "",
                "test_result": _build_test_result(dev_task.test_result),
                "files_changed": dev_task.files_changed or [],
                "attempt_number": review_task.attempt_number,
                "previous_rejections": [],  # 2.3 携带历次 rejection
                # 1.5c 注入 repo_profile（1.5b 阶段为空）
                "repo_style_notes": "",
                "repo_contributing_summary": "",
            }
            empty_diff = not agent_input_kwargs["diff_content"].strip()

        # ---- 早期失败：diff 为空 → 直接 REJECTED ----
        if empty_diff:
            log.warning("review_worker.empty_diff", dev_task_id=str(dev_task_id))
            await _write_rejection(
                issue_id=issue_id,
                review_task_id=review_task_id,
                dim=RejectionDimension.CORRECTNESS,
                category=RejectionCategory.OTHER,
                severity=RejectionSeverity.BLOCKER,
                detail="Agent B reported success but produced no diff.",
                raw_text="empty_diff sentinel",
            )
            return {"verdict": "REJECTED", "reason": "empty_diff"}

        # ---- Phase 3: Agent C 调用（不持 DB 锁）----
        agent_input = AgentCInput(**agent_input_kwargs)
        output, llm_resp = await agent.review(agent_input)

        # ---- Phase 4: 写结果 ----
        async with session_scope() as s:
            svc = IssueService(s)
            issue = await svc.get(issue_id)
            review_task = await s.get(ReviewTask, review_task_id)
            if review_task is None:
                log.error(
                    "review_worker.review_task_lost_after_llm",
                    review_task_id=str(review_task_id),
                )
                return {"error": "review_task_missing_after_llm"}

            review_task.finished_at = datetime.now(timezone.utc)
            review_task.status = ReviewTaskStatus.COMPLETED
            review_task.verdict = ReviewVerdict(output.verdict)
            review_task.overall_score = output.overall_score
            review_task.dimensions = {
                k: {
                    "score": v.score,
                    "passed": v.passed,
                    "comment": v.comment,
                }
                for k, v in output.dimensions.items()
            }
            review_task.rejection_reason = output.rejection_reason
            review_task.pr_title = output.pr_title
            review_task.pr_body = output.pr_body
            review_task.overall_comment = output.overall_comment
            review_task.provider = llm_resp.provider
            review_task.model = llm_resp.model
            review_task.input_tokens = llm_resp.final_attempt.input_tokens
            review_task.output_tokens = llm_resp.final_attempt.output_tokens
            review_task.cost_usd = sum(a.cost_usd for a in llm_resp.all_attempts)
            review_task.is_fallback = llm_resp.is_fallback

            await persist_attempts(
                s,
                llm_resp.all_attempts,
                agent_kind=AgentKind.AGENT_C,
                issue_id=issue.id,
                prompt_version=agent.prompt_version,
            )

            if output.verdict == "APPROVED":
                # 状态保持 IN_REVIEW，1.5d 的 PR 创建链路接力
                log.info(
                    "review_worker.approved",
                    issue_id=str(issue_id),
                    review_task_id=str(review_task_id),
                    overall_score=output.overall_score,
                )
            else:
                # REJECTED → 写 rejection_reasons + 转 REVIEW_REJECTED
                _add_rejection_inline(
                    session=s,
                    issue=issue,
                    review_task=review_task,
                    output=output,
                )
                try:
                    await svc.mark_review_rejected(issue)
                except InvalidTransitionError as e:
                    log.error("review_worker.transition_failed", error=str(e))

        log.info(
            "review_worker.done",
            issue_id=str(issue_id),
            review_task_id=str(review_task_id),
            verdict=output.verdict,
            cost=sum(a.cost_usd for a in llm_resp.all_attempts),
        )
        return {
            "verdict": output.verdict,
            "overall_score": output.overall_score,
            "review_task_id": str(review_task_id),
            "is_fallback": llm_resp.is_fallback,
        }

    except (ToolNotCalledError, SchemaValidationError) as e:
        # 模型层面输出问题：review_task FAILED + issue → REVIEW_REJECTED
        await _mark_review_failed(
            issue_id=issue_id,
            review_task_id=review_task_id,
            reason=type(e).__name__,
            detail=str(e)[:500],
        )
        log.error(
            "review_worker.model_output_invalid",
            issue_id=str(issue_id),
            error=str(e)[:300],
        )
        return {"error": "model_output_invalid"}

    except InvalidTransitionError as e:
        log.error("review_worker.invalid_transition", error=str(e))
        return {"error": "invalid_transition"}

    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate_body(body: str | None, *, head: int = 1500, tail: int = 300) -> str:
    text = body or ""
    if len(text) <= head + tail:
        return text
    return text[:head] + "\n\n[... truncated ...]\n\n" + text[-tail:]


def _prepare_diff(diff: str | None, *, max_chars: int = 60_000) -> str:
    """diff 截断：超过 max_chars 时取头 + 尾，避免 prompt 爆炸。

    60k chars ≈ 20k tokens，给 Agent C 留下评分推理空间。
    """
    text = diff or ""
    if len(text) <= max_chars:
        return text
    head = max_chars * 4 // 5
    tail = max_chars - head
    return text[:head] + "\n\n[... diff truncated ...]\n\n" + text[-tail:]


def _build_test_result(test_result: dict | None) -> TestResult:
    tr = test_result or {}
    return TestResult(
        test_passed=bool(tr.get("test_passed", False)),
        total_tests=int(tr.get("total_tests", 0)),
        failed_tests=int(tr.get("failed_tests", 0)),
        new_tests_added=int(tr.get("new_tests_added", 0)),
        test_output_snippet=str(tr.get("test_output_snippet", "")),
    )


def _add_rejection_inline(
    *,
    session,
    issue: Issue,
    review_task: ReviewTask,
    output: AgentCOutput,
) -> None:
    """Agent C REJECTED 时写 rejection_reasons 一条。"""
    dim, category, severity = _classify_from_dimensions(output)
    rejection = RejectionReason(
        issue_id=issue.id,
        pr_id=None,                # 尚未创建 PR
        review_task_id=review_task.id,
        source=RejectionSource.AGENT_C,
        category=category,
        severity=severity,
        dimension=dim,
        agent_b_attribution=AgentBAttribution.YES,
        detail=(output.rejection_reason or output.overall_comment or "")[:2000],
        raw_text=output.overall_comment,
        classified_by="agent_c",
    )
    session.add(rejection)


def _classify_from_dimensions(
    output: AgentCOutput,
) -> tuple[RejectionDimension | None, RejectionCategory, RejectionSeverity]:
    """根据 Agent C 5 维评分的最低未通过维度，推断 rejection category/severity。

    1.5b 简化逻辑；2.3 的 RejectionClassifier 会做更细致的二次分类。
    """
    dims = output.dimensions
    # 按 schema 必有 5 维；按顺序找第一个 passed=False
    priority = ("correctness", "security", "test_coverage", "code_style", "pr_description")
    for name in priority:
        d = dims.get(name)
        if d is None or d.passed:
            continue
        if name == "correctness":
            return (
                RejectionDimension.CORRECTNESS,
                RejectionCategory.WRONG_ROOT_CAUSE,
                RejectionSeverity.BLOCKER,
            )
        if name == "security":
            return (
                RejectionDimension.SECURITY,
                RejectionCategory.SECURITY_CONCERN,
                RejectionSeverity.BLOCKER,
            )
        if name == "test_coverage":
            return (
                RejectionDimension.TEST_COVERAGE,
                RejectionCategory.INCOMPLETE_FIX,
                RejectionSeverity.MAJOR,
            )
        if name == "code_style":
            return (
                RejectionDimension.CODE_STYLE,
                RejectionCategory.STYLE_MISMATCH,
                RejectionSeverity.MAJOR,
            )
        if name == "pr_description":
            return (
                RejectionDimension.PR_DESCRIPTION,
                RejectionCategory.OTHER,
                RejectionSeverity.MINOR,
            )
    # 全部维度 passed=true 但 verdict=REJECTED（不应发生，但保底）
    return (None, RejectionCategory.OTHER, RejectionSeverity.MAJOR)


async def _write_rejection(
    *,
    issue_id: uuid.UUID,
    review_task_id: uuid.UUID | None,
    dim: RejectionDimension | None,
    category: RejectionCategory,
    severity: RejectionSeverity,
    detail: str,
    raw_text: str | None,
) -> None:
    """独立事务写 rejection + 转 issue REVIEW_REJECTED + review_task FAILED→COMPLETED 合成。"""
    async with session_scope() as s:
        svc = IssueService(s)
        try:
            issue = await svc.get(issue_id)
        except IssueNotFoundError:
            return

        rejection = RejectionReason(
            issue_id=issue.id,
            pr_id=None,
            review_task_id=review_task_id,
            source=RejectionSource.AGENT_C,
            category=category,
            severity=severity,
            dimension=dim,
            agent_b_attribution=AgentBAttribution.YES,
            detail=detail[:2000],
            raw_text=raw_text,
            classified_by="agent_c",
        )
        s.add(rejection)

        if review_task_id is not None:
            review_task = await s.get(ReviewTask, review_task_id)
            if review_task is not None:
                review_task.finished_at = datetime.now(timezone.utc)
                review_task.status = ReviewTaskStatus.COMPLETED
                review_task.verdict = ReviewVerdict.REJECTED
                review_task.rejection_reason = detail

        if issue.status == IssueStatus.IN_REVIEW:
            try:
                await svc.mark_review_rejected(issue)
            except InvalidTransitionError as e:
                log.error("review_worker.transition_failed", error=str(e))


async def _mark_review_failed(
    *,
    issue_id: uuid.UUID,
    review_task_id: uuid.UUID | None,
    reason: str,
    detail: str,
) -> None:
    """review_task 标 FAILED + issue 转 REVIEW_REJECTED（如可转）。"""
    async with session_scope() as s:
        svc = IssueService(s)
        try:
            issue = await svc.get(issue_id)
        except IssueNotFoundError:
            return

        if review_task_id is not None:
            review_task = await s.get(ReviewTask, review_task_id)
            if review_task is not None:
                review_task.finished_at = datetime.now(timezone.utc)
                review_task.status = ReviewTaskStatus.FAILED
                review_task.failure_reason = reason[:100]
                review_task.failure_detail = detail

        if issue.status == IssueStatus.IN_REVIEW:
            try:
                await svc.mark_review_rejected(issue)
            except InvalidTransitionError as e:
                log.error("review_worker.transition_failed_failed_branch", error=str(e))
