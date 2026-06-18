"""dev_worker — Agent B Celery 任务。

任务流程：
    Phase 1 (DB 准备):
        1. 加载 Issue + DevTask，校验状态
        2. Fork / 跳过 fork + 生成分支名
        3. 构造 AgentBInput + prompt（base64 编码）
        4. Issue 状态转 IN_DEV，DevTask 状态转 RUNNING

    Phase 2 (沙箱执行 + 实时日志):
        5. 启动 Docker 容器（SandboxManager.start）
        6. 线程读取容器日志 → asyncio.Queue → DevLog DB + Redis PubSub
        7. 从 stream-json 提取 report_completion / report_failure
        8. 超时则强制停止容器

    Phase 3 (结果处理):
        9. 停止并清理容器
        10. 写 DevTask 结果字段
        11. Issue 状态转 QUEUED_REVIEW（成功）或 DEV_FAILED（失败/超时）

Celery + async：asyncio.run() 在每个 task 内起独立 event loop。
"""
from __future__ import annotations

import asyncio
import base64
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis
import structlog
from celery import Task

from app.agents.agent_b import AgentB
from app.agents.prompts.agent_b import SYSTEM_PROMPT, build_prompt
from app.agents.schemas import AgentBFailure, AgentBInput, AgentBOutput
from app.core.config import get_settings
from app.db.database import session_scope
from app.models.dev_task import DevTask
from app.models.enums import (
    DevLogLevel,
    DevLogStep,
    DevTaskStatus,
    IssueStatus,
)
from app.models.issue import Issue
from app.sandbox.manager import SandboxError, SandboxManager
from app.services import dev_log_service
from app.services.github_service import GitHubService, GitHubServiceError
from app.services.issue_service import (
    InvalidTransitionError,
    IssueNotFoundError,
    IssueService,
)
from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)

_sandbox = SandboxManager()
_agent_b_parser = AgentB()


@celery_app.task(
    name="app.workers.dev_worker.develop_issue",
    bind=True,
    acks_late=True,
    max_retries=0,  # 不自动重试；重试逻辑在 Phase 3 / 2.3 里
)
def develop_issue(self: Task, issue_id: str, dev_task_id: str) -> dict[str, object]:
    """Celery 同步包装：内部跑 asyncio.run。"""
    return asyncio.run(
        _develop_issue_async(uuid.UUID(issue_id), uuid.UUID(dev_task_id))
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


async def _develop_issue_async(
    issue_id: uuid.UUID,
    dev_task_id: uuid.UUID,
) -> dict[str, object]:
    settings = get_settings()
    timeout_sec = settings.dev_task_timeout_minutes * 60

    # ---- Phase 1: 准备 ----
    setup_result = await _setup_phase(issue_id, dev_task_id, settings)
    if setup_result.get("skipped"):
        return setup_result

    env = setup_result["env"]
    image = setup_result["image"]

    # ---- Phase 2: 沙箱 ----
    sandbox_result = await _sandbox_phase(
        issue_id=issue_id,
        dev_task_id=dev_task_id,
        env=env,
        image=image,
        timeout_sec=timeout_sec,
        settings=settings,
    )

    # ---- Phase 3: 结果 ----
    return await _result_phase(
        issue_id=issue_id,
        dev_task_id=dev_task_id,
        sandbox_result=sandbox_result,
    )


# ---------------------------------------------------------------------------
# Phase 1: DB 准备 + 环境变量构造
# ---------------------------------------------------------------------------


async def _setup_phase(
    issue_id: uuid.UUID,
    dev_task_id: uuid.UUID,
    settings: Any,
) -> dict[str, Any]:
    async with session_scope() as s:
        # 加载 Issue + DevTask
        issue = await s.get(Issue, issue_id)
        if issue is None:
            log.error("dev_worker.issue_missing", issue_id=str(issue_id))
            return {"skipped": True, "error": "issue_not_found"}

        dev_task = await s.get(DevTask, dev_task_id)
        if dev_task is None:
            log.error("dev_worker.dev_task_missing", dev_task_id=str(dev_task_id))
            return {"skipped": True, "error": "dev_task_not_found"}

        if issue.status != IssueStatus.QUEUED_DEV:
            log.info(
                "dev_worker.skip_not_queued",
                issue_id=str(issue_id),
                status=issue.status.value,
            )
            return {"skipped": True, "status": issue.status.value}

        if dev_task.status != DevTaskStatus.PENDING:
            log.info(
                "dev_worker.skip_task_not_pending",
                dev_task_id=str(dev_task_id),
                task_status=dev_task.status.value,
            )
            return {"skipped": True, "task_status": dev_task.status.value}

        # 加载关联数据
        await s.refresh(issue, ["repository", "evaluation"])
        repo = issue.repository
        evaluation = issue.evaluation

        # 1.5c: 拉 repo_profile（缺失 / 过期不阻塞，让 prompt 走自学习降级）
        from app.models.repo_profile import RepoProfile
        from sqlalchemy import select as _select

        profile_stmt = _select(RepoProfile).where(RepoProfile.repo_id == repo.id)
        profile_row = (await s.execute(profile_stmt)).scalar_one_or_none()
        profile_block = _render_profile_block(profile_row)

        # 生成分支名
        branch_name = GitHubService.generate_branch_name(issue.github_number, issue.title)

        # Fork repo（如果配置了 dev token）
        github_svc = GitHubService.from_settings()
        forked_repo = repo.full_name
        try:
            forked_repo = await github_svc.fork_repo(repo.full_name)
        except GitHubServiceError as e:
            log.warning("dev_worker.fork_failed", error=str(e), fallback=repo.full_name)

        # 构造 AgentBInput
        eval_summary = (evaluation.summary if evaluation else "") or ""
        agent_input = AgentBInput(
            issue_title=issue.title,
            issue_body=_truncate_body(issue.body),
            issue_url=issue.github_url,
            repo_full_name=forked_repo,
            repo_language=repo.primary_language,
            evaluation_summary=eval_summary,
            review_context=dev_task.review_context,
            attempt_number=dev_task.attempt_number,
            branch_name=branch_name,
            forked_repo=forked_repo,
            repo_profile_block=profile_block,
        )

        # 构造 prompt 并 base64 编码
        full_prompt = f"{SYSTEM_PROMPT}\n\n{build_prompt(agent_input)}"
        prompt_b64 = base64.b64encode(full_prompt.encode("utf-8")).decode("ascii")

        # 选择沙箱镜像
        lang = (repo.primary_language or "python").lower()
        image = f"{settings.sandbox_image_prefix}-{lang}:latest"
        if not _sandbox.image_exists(image):
            image = f"{settings.sandbox_image_prefix}-python:latest"
            log.info("dev_worker.image_fallback", lang=lang, using=image)

        # 构造容器 env
        token_val = settings.github_token.get_secret_value() if settings.github_token else ""
        dev_token_val = (
            settings.github_dev_token.get_secret_value() if settings.github_dev_token else ""
        )
        api_key_val = (
            settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else ""
        )
        base_url_val = settings.anthropic_base_url or ""
        auth_token_val = (
            settings.anthropic_auth_token.get_secret_value()
            if settings.anthropic_auth_token
            else ""
        )

        env: dict[str, str] = {
            "REPO_FULL_NAME": forked_repo,
            "BRANCH_NAME": branch_name,
            "GITHUB_TOKEN": token_val,
            "GITHUB_DEV_TOKEN": dev_token_val,
            "AGENT_PROMPT_B64": prompt_b64,
            "CLAUDE_MODEL": "claude-sonnet-4-6",
            "MAX_TURNS": str(settings.agent_b_max_turns),
            "DISABLE_AUTOUPDATER": "1",
        }
        if api_key_val:
            env["ANTHROPIC_API_KEY"] = api_key_val
        if base_url_val:
            env["ANTHROPIC_BASE_URL"] = base_url_val
        if auth_token_val:
            env["ANTHROPIC_AUTH_TOKEN"] = auth_token_val

        # 状态转换
        svc = IssueService(s)
        await svc.mark_in_dev(issue)

        dev_task.status = DevTaskStatus.RUNNING
        dev_task.started_at = datetime.now(timezone.utc)
        dev_task.sandbox_image = image
        dev_task.forked_repo = forked_repo
        dev_task.branch_name = branch_name

    log.info(
        "dev_worker.setup_done",
        issue_id=str(issue_id),
        dev_task_id=str(dev_task_id),
        image=image,
        forked_repo=forked_repo,
        branch=branch_name,
    )
    return {"env": env, "image": image, "skipped": False}


# ---------------------------------------------------------------------------
# Phase 2: 沙箱执行 + 实时日志流
# ---------------------------------------------------------------------------


async def _sandbox_phase(
    issue_id: uuid.UUID,
    dev_task_id: uuid.UUID,
    env: dict[str, str],
    image: str,
    timeout_sec: int,
    settings: Any,
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1)

    # 启动容器
    try:
        container = await loop.run_in_executor(
            executor,
            lambda: _sandbox.start(image=image, dev_task_id=dev_task_id, env=env),
        )
    except SandboxError as e:
        log.error("dev_worker.container_start_failed", error=str(e))
        return {
            "success": False,
            "failure_reason": "sandbox_start_failed",
            "failure_detail": str(e),
            "timed_out": False,
            "total_cost_usd": 0.0,
            "num_turns": 0,
        }

    # 记录 container_id
    async with session_scope() as s:
        dev_task = await s.get(DevTask, dev_task_id)
        if dev_task:
            dev_task.container_id = container.short_id

    log.info("dev_worker.container_started", container_id=container.short_id)

    # 实时日志流
    log_queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=500)

    def _sync_reader() -> None:
        try:
            for line in _sandbox.stream_logs(container):
                fut = asyncio.run_coroutine_threadsafe(log_queue.put(line), loop)
                fut.result(timeout=15)
        except Exception as exc:
            log.warning("dev_worker.reader_error", error=str(exc))
        finally:
            asyncio.run_coroutine_threadsafe(log_queue.put(None), loop).result(timeout=15)

    reader_fut = loop.run_in_executor(executor, _sync_reader)

    report_kind: str | None = None
    report_payload: dict[str, Any] | None = None
    total_cost_usd = 0.0
    num_turns = 0
    timed_out = False

    redis_client = aioredis.from_url(settings.redis_url, decode_responses=False)
    try:
        async with asyncio.timeout(timeout_sec + 120):
            async with session_scope() as s:
                while True:
                    try:
                        line = await asyncio.wait_for(log_queue.get(), timeout=30)
                    except asyncio.TimeoutError:
                        continue
                    if line is None:
                        break
                    parsed = _agent_b_parser.parse_stream_line(line)
                    if parsed.message:
                        await dev_log_service.append(
                            s,
                            redis_client,
                            dev_task_id=dev_task_id,
                            level=parsed.level,
                            step=parsed.step,
                            message=parsed.message,
                        )
                    if parsed.report_kind and report_kind is None:
                        report_kind = parsed.report_kind
                        report_payload = parsed.report_payload
                    if parsed.total_cost_usd:
                        total_cost_usd = parsed.total_cost_usd
                        num_turns = parsed.num_turns or num_turns

    except asyncio.TimeoutError:
        timed_out = True
        log.warning(
            "dev_worker.timeout",
            dev_task_id=str(dev_task_id),
            timeout_sec=timeout_sec,
        )
        await loop.run_in_executor(executor, lambda: _sandbox.stop(container))
        async with session_scope() as s:
            await dev_log_service.append(
                s,
                redis_client,
                dev_task_id=dev_task_id,
                level=DevLogLevel.ERROR,
                step=DevLogStep.SYSTEM,
                message=f"[timeout] Task exceeded {settings.dev_task_timeout_minutes} minutes. Container killed.",
            )
    finally:
        await redis_client.aclose()

    # 等待 reader 线程结束
    try:
        await asyncio.wait_for(asyncio.wrap_future(reader_fut), timeout=30)
    except Exception:
        pass

    # 若 stream-json 中未检测到 report，尝试从文件读取
    if not report_kind and not timed_out:
        file_report = await loop.run_in_executor(
            executor, lambda: _sandbox.collect_report(container)
        )
        if file_report:
            report_kind = file_report.get("kind")
            report_payload = file_report.get("payload")

    # 1.5b: 采集 git diff（容器仍在但已退出，文件系统可读）
    git_diff: str | None = None
    if not timed_out:
        git_diff = await loop.run_in_executor(
            executor, lambda: _sandbox.collect_diff(container)
        )

    # 停止并清理容器
    if not timed_out:
        await loop.run_in_executor(executor, lambda: _sandbox.stop(container))

    executor.shutdown(wait=False)

    return {
        "success": report_kind == "completion"
        and bool(report_payload)
        and report_payload.get("test_passed", False),
        "report_kind": report_kind,
        "report_payload": report_payload,
        "timed_out": timed_out,
        "total_cost_usd": total_cost_usd,
        "num_turns": num_turns,
        "git_diff": git_diff,
        "failure_reason": None,
        "failure_detail": None,
    }


# ---------------------------------------------------------------------------
# Phase 3: 写结果 + Issue 状态转换
# ---------------------------------------------------------------------------


async def _result_phase(
    issue_id: uuid.UUID,
    dev_task_id: uuid.UUID,
    sandbox_result: dict[str, Any],
) -> dict[str, object]:
    success = sandbox_result.get("success", False)
    timed_out = sandbox_result.get("timed_out", False)
    report_kind = sandbox_result.get("report_kind")
    report_payload = sandbox_result.get("report_payload") or {}
    total_cost_usd = sandbox_result.get("total_cost_usd", 0.0)
    num_turns = sandbox_result.get("num_turns", 0)
    git_diff = sandbox_result.get("git_diff")
    failure_reason = sandbox_result.get("failure_reason")
    failure_detail = sandbox_result.get("failure_detail")

    # 是否需要触发 review_worker
    enqueue_review = False

    async with session_scope() as s:
        svc = IssueService(s)
        issue = await svc.get(issue_id)
        dev_task = await s.get(DevTask, dev_task_id)
        if dev_task is None:
            log.error("dev_worker.dev_task_missing_result", dev_task_id=str(dev_task_id))
            return {"error": "dev_task_missing"}

        now = datetime.now(timezone.utc)
        dev_task.finished_at = now
        dev_task.total_cost_usd = total_cost_usd
        dev_task.loop_iterations = num_turns
        if git_diff is not None:
            dev_task.git_diff = git_diff

        if success:
            # 成功路径：IN_DEV → DEV_TESTING → QUEUED_REVIEW
            output = _agent_b_parser.extract_output(report_kind, report_payload)
            if isinstance(output, AgentBOutput):
                dev_task.files_changed = output.files_changed
                dev_task.diff_summary = output.diff_summary
                dev_task.test_result = {
                    "test_passed": output.test_passed,
                    "total_tests": output.total_tests,
                    "failed_tests": output.failed_tests,
                    "new_tests_added": output.new_tests_added,
                    "test_output_snippet": output.test_output_snippet,
                }
            dev_task.status = DevTaskStatus.SUCCEEDED
            try:
                await svc.mark_dev_testing(issue)
                await svc.mark_queued_review(issue)
                enqueue_review = True
            except InvalidTransitionError as e:
                log.error("dev_worker.transition_failed", error=str(e))

        else:
            # 失败路径：IN_DEV → DEV_FAILED
            if timed_out:
                dev_task.status = DevTaskStatus.TIMEOUT
                dev_task.failure_reason = "timeout"
                dev_task.failure_detail = f"Exceeded {_get_timeout_minutes()} minutes."
            else:
                output = _agent_b_parser.extract_output(report_kind, report_payload)
                if isinstance(output, AgentBFailure):
                    dev_task.failure_reason = output.reason
                    dev_task.failure_detail = output.detail
                elif failure_reason:
                    dev_task.failure_reason = failure_reason
                    dev_task.failure_detail = failure_detail
                else:
                    dev_task.failure_reason = report_kind or "no_report"
                    dev_task.failure_detail = "Agent B did not call report_completion."
                dev_task.status = DevTaskStatus.FAILED

            try:
                await svc.mark_dev_failed(issue)
            except InvalidTransitionError as e:
                log.error("dev_worker.transition_failed", error=str(e))

    # 1.5b: 成功路径下触发 review_worker（必须在 DB session 关闭后发，
    # 否则 worker 可能比 commit 还快读到旧状态）
    if enqueue_review:
        celery_app.send_task(
            "app.workers.review_worker.review_dev_task",
            args=[str(issue_id), str(dev_task_id)],
            queue="review_queue",
        )

    log.info(
        "dev_worker.done",
        issue_id=str(issue_id),
        dev_task_id=str(dev_task_id),
        success=success,
        cost=total_cost_usd,
        turns=num_turns,
        review_enqueued=enqueue_review,
    )
    return {
        "success": success,
        "total_cost_usd": total_cost_usd,
        "num_turns": num_turns,
        "failure_reason": dev_task.failure_reason if not success else None,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate_body(body: str | None, *, head: int = 1500, tail: int = 300) -> str:
    text = body or ""
    if len(text) <= head + tail:
        return text
    return text[:head] + "\n\n[... truncated ...]\n\n" + text[-tail:]


def _render_profile_block(profile) -> str | None:  # type: ignore[no-untyped-def]
    """RepoProfile → 注入 Agent B 的纯文本块。None 让 prompt 走自学习降级。"""
    if profile is None:
        return None
    parts: list[str] = [
        f"test_command: {profile.test_command or '(unknown)'}",
        f"install_command: {profile.install_command or '(unknown)'}",
    ]
    if profile.lint_command:
        parts.append(f"lint_command: {profile.lint_command}")
    parts.append(f"pr_title_convention: {profile.pr_title_convention or '(none)'}")
    parts.append(
        f"profile_quality: {profile.profile_quality.value} ({profile.quality_reason or 'no reason'})"
    )
    if profile.code_style_notes:
        parts.append(f"code_style_notes: {profile.code_style_notes}")
    if profile.contributing_summary:
        parts.append(f"contributing_summary: {profile.contributing_summary}")
    if profile.forbidden_patterns:
        parts.append("forbidden_patterns:")
        for p in profile.forbidden_patterns:
            parts.append(f"  - {p}")
    return "\n".join(parts)


def _get_timeout_minutes() -> int:
    return get_settings().dev_task_timeout_minutes
