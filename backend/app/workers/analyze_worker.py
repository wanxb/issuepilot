"""analyze_worker — Agent A Celery 任务。

任务流程：
    1. 加载 Issue + Repository
    2. 状态转 ANALYZING
    3. 构造 AgentAInput → 调 AgentA.analyze()
    4. 把所有 LLM attempts 写 llm_call_logs（best-effort）
    5. 写 evaluations 行 + 状态转 PENDING_DECISION

异常处理：
    - LLMCallError / ToolNotCalledError / SchemaValidationError：状态回退到 DISCOVERED，
      retry_count+1，任务标记失败让 Celery 按配置重试（1.2d 用 Celery 默认重试一次）
    - InvalidTransitionError：任务直接失败不重试（语义错误）

Celery + async：用 asyncio.run() 在 task 内启 event loop（每任务一个）。
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import structlog
from celery import Task

from app.agents.agent_a import (
    AgentA,
    SchemaValidationError,
    ToolNotCalledError,
)
from app.agents.schemas import AgentAInput
from app.db.database import session_scope
from app.llm.base import LLMCallError
from app.llm.factory import build_client
from app.llm.logging import persist_attempts
from app.models.enums import AgentKind, IssueDifficulty, IssueStatus
from app.services.issue_service import (
    InvalidTransitionError,
    IssueNotFoundError,
    IssueService,
)
from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)


@celery_app.task(
    name="app.workers.analyze_worker.analyze_issue",
    bind=True,
    autoretry_for=(LLMCallError,),
    max_retries=1,
    default_retry_delay=10,
    acks_late=True,
)
def analyze_issue(self: Task, issue_id: str) -> dict[str, object]:
    """Celery 同步包装：内部跑 asyncio.run。"""
    return asyncio.run(_analyze_issue_async(uuid.UUID(issue_id)))


async def _analyze_issue_async(issue_id: uuid.UUID) -> dict[str, object]:
    client = build_client("agent_a")
    try:
        agent = AgentA(client)

        # ---- 阶段 1: load + 状态转 ANALYZING ----
        async with session_scope() as s:
            svc = IssueService(s)
            issue = await svc.get(issue_id)
            repo = issue.repository
            if issue.status != IssueStatus.DISCOVERED:
                log.info(
                    "analyze_worker.skip_not_discovered",
                    issue_id=str(issue_id),
                    status=issue.status.value,
                )
                return {"skipped": True, "status": issue.status.value}
            await svc.mark_analyzing(issue)
            agent_input = AgentAInput(
                issue_title=issue.title,
                issue_body=_truncate_body(issue.body),
                issue_labels=issue.labels or [],
                issue_url=issue.github_url,
                repo_full_name=repo.full_name,
                repo_description=repo.description,
                repo_language=repo.primary_language,
                repo_stars=repo.stars,
                repo_topics=repo.topics or [],
                repo_last_commit_days=_days_since(repo.last_commit_at),
                repo_open_prs_count=repo.open_prs_count,
                repo_merged_prs_last_30d=repo.merged_prs_last_30d,
            )

        # ---- 阶段 2: 调 Agent A（session 已关，DB 不持锁等 LLM）----
        output, llm_resp = await agent.analyze(agent_input)

        # ---- 阶段 3: 写 evaluation + llm_call_logs + 状态转 PENDING_DECISION ----
        async with session_scope() as s:
            svc = IssueService(s)
            issue = await svc.get(issue_id)
            eval_payload = {
                "total_score": output.total_score,
                "difficulty": IssueDifficulty(output.difficulty),
                "estimated_hours": output.estimated_hours,
                "summary": output.summary,
                "recommendation": output.recommendation,
                "is_worth_developing": output.is_worth_developing,
                "dimensions": {
                    k: {"score": v.score, "comment": v.comment}
                    for k, v in output.dimensions.items()
                },
                "provider": llm_resp.provider,
                "model": llm_resp.model,
                "prompt_version": agent.prompt_version,
                "input_tokens": llm_resp.final_attempt.input_tokens,
                "output_tokens": llm_resp.final_attempt.output_tokens,
                "cost_usd": sum(a.cost_usd for a in llm_resp.all_attempts),
                "is_fallback": llm_resp.is_fallback,
            }
            evaluation = await svc.finish_analyzing(issue, eval_payload=eval_payload)
            await persist_attempts(
                s,
                llm_resp.all_attempts,
                agent_kind=AgentKind.AGENT_A,
                issue_id=issue.id,
                prompt_version=agent.prompt_version,
            )
            log.info(
                "analyze_worker.done",
                issue_id=str(issue_id),
                evaluation_id=str(evaluation.id),
                total_score=output.total_score,
                provider=llm_resp.provider,
                is_fallback=llm_resp.is_fallback,
            )
            return {
                "evaluation_id": str(evaluation.id),
                "total_score": output.total_score,
                "is_worth_developing": output.is_worth_developing,
                "is_fallback": llm_resp.is_fallback,
            }

    except (ToolNotCalledError, SchemaValidationError) as e:
        # 模型层面输出问题：回退到 DISCOVERED，retry_count+1
        async with session_scope() as s:
            issue = await s.get(__import__("app.models", fromlist=["Issue"]).Issue, issue_id)
            if issue is not None:
                issue.status = IssueStatus.DISCOVERED
                issue.retry_count += 1
        log.error(
            "analyze_worker.model_output_invalid",
            issue_id=str(issue_id),
            error=str(e)[:300],
        )
        raise

    except IssueNotFoundError:
        log.error("analyze_worker.issue_missing", issue_id=str(issue_id))
        return {"error": "not_found"}

    except InvalidTransitionError as e:
        log.error("analyze_worker.invalid_transition", error=str(e))
        return {"error": "invalid_transition"}

    finally:
        await client.aclose()


def _truncate_body(body: str | None, *, head: int = 1500, tail: int = 300) -> str:
    """超过 head+tail 时截取前 head + 后 tail（详见 AGENT_DESIGN §Agent A 输入约定）。"""
    text = body or ""
    if len(text) <= head + tail:
        return text
    return text[:head] + "\n\n[... truncated ...]\n\n" + text[-tail:]


def _days_since(dt: datetime | None) -> int:
    if dt is None:
        return 9999
    now = datetime.now(timezone.utc)
    delta = now - dt
    return max(0, delta.days)
