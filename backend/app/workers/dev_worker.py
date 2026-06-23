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

        # 2.1: 选择沙箱镜像（语言映射 + 镜像存在性双重 fallback）
        from app.sandbox.language import sandbox_image_for_language

        image, lang = sandbox_image_for_language(
            image_prefix=settings.sandbox_image_prefix,
            primary_language=repo.primary_language,
            image_exists_check=_sandbox.image_exists,
        )

        # 构造容器 env
        token_val = settings.github_token.get_secret_value() if settings.github_token else ""
        dev_token_val = (
            settings.github_dev_token.get_secret_value() if settings.github_dev_token else ""
        )

        # 2.3: 任务级 fallback —— retry 时切到 models.yaml.agent_b.fallback
        llm_env, model_label = build_sandbox_llm_env(
            settings, use_fallback=dev_task.use_fallback_provider,
        )

        # 3.2 Extended Thinking 切换
        from app.sandbox.extended_thinking import should_enable_extended_thinking
        eval_difficulty = (
            evaluation.difficulty if evaluation and evaluation.difficulty else None
        )
        ext_enabled, ext_reason = should_enable_extended_thinking(
            evaluation_difficulty=eval_difficulty,
            attempt_number=dev_task.attempt_number or 1,
            enable_difficulties_raw=settings.agent_b_extended_thinking_difficulties,
            min_attempt=settings.agent_b_extended_thinking_min_attempt,
        )

        env: dict[str, str] = {
            "REPO_FULL_NAME": forked_repo,
            "BRANCH_NAME": branch_name,
            "GITHUB_TOKEN": token_val,
            "GITHUB_DEV_TOKEN": dev_token_val,
            "AGENT_PROMPT_B64": prompt_b64,
            "CLAUDE_MODEL": model_label,
            "MAX_TURNS": str(settings.agent_b_max_turns),
            "DISABLE_AUTOUPDATER": "1",
            "ENABLE_EXTENDED_THINKING": "1" if ext_enabled else "0",
            **llm_env,
        }
        log.info(
            "dev_worker.extended_thinking",
            issue_id=str(issue_id),
            enabled=ext_enabled, reason=ext_reason,
        )

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
    stuck_detected = False

    # 2.3 卡死检测
    from app.agents.stuck_detector import StuckDetector
    stuck_window = settings.agent_b_stuck_window
    stuck_detector = (
        StuckDetector(window_size=stuck_window) if stuck_window > 0 else None
    )

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
                    # 2.3 卡死检测：观察工具调用并在 stuck 时主动 stop
                    if stuck_detector is not None and parsed.tool_name:
                        stuck_detector.observe(parsed.tool_name)
                        if stuck_detector.is_stuck():
                            stuck_detected = True
                            log.warning(
                                "dev_worker.stuck",
                                dev_task_id=str(dev_task_id),
                                window=stuck_detector.snapshot(),
                            )
                            await dev_log_service.append(
                                s, redis_client, dev_task_id=dev_task_id,
                                level=DevLogLevel.ERROR, step=DevLogStep.SYSTEM,
                                message=(
                                    f"[stuck] Agent B has read {stuck_window} "
                                    f"tools in a row without any write/edit/test. "
                                    f"Killing container."
                                ),
                            )
                            await loop.run_in_executor(
                                executor, lambda: _sandbox.stop(container),
                            )
                            break

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
    branch_pushed: bool | None = None
    if not timed_out:
        git_diff = await loop.run_in_executor(
            executor, lambda: _sandbox.collect_diff(container)
        )
        # 1.5d: 采集 push 状态
        branch_pushed = await loop.run_in_executor(
            executor, lambda: _sandbox.collect_push_status(container)
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
        "stuck_detected": stuck_detected,
        "total_cost_usd": total_cost_usd,
        "num_turns": num_turns,
        "git_diff": git_diff,
        "branch_pushed": branch_pushed,
        "failure_reason": "stuck" if stuck_detected else None,
        "failure_detail": (
            f"Killed after {stuck_window} consecutive read-only tool calls."
            if stuck_detected else None
        ),
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
    branch_pushed = sandbox_result.get("branch_pushed")
    failure_reason = sandbox_result.get("failure_reason")
    failure_detail = sandbox_result.get("failure_detail")

    # 是否需要触发 review_worker / dev_worker retry
    enqueue_review = False
    retry_ctx: dict[str, Any] | None = None

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
        if branch_pushed is not None:
            dev_task.branch_pushed = branch_pushed

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
                # 3.2: 修改影响范围检查
                from app.sandbox.scope_check import scope_check
                eval_summary_text = (
                    issue.evaluation.summary if issue.evaluation else None
                )
                dev_task.scope_check = scope_check(
                    files_changed=output.files_changed,
                    issue_body=issue.body,
                    evaluation_summary=eval_summary_text,
                )
                if dev_task.scope_check.get("suspicious"):
                    log.warning(
                        "dev_worker.scope_check_suspicious",
                        issue_id=str(issue_id),
                        files_changed=output.files_changed,
                        referenced=dev_task.scope_check.get("referenced_in_issue"),
                    )
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

            # 2.3: Agent B 重试机制——按 attempt_number vs max_dev_retry
            # 决定是否建新 DevTask 重入（仅在转 DEV_FAILED 成功后才考虑重试）
            if issue.status == IssueStatus.DEV_FAILED:
                retry_ctx = await _enqueue_dev_retry_or_stop(
                    session=s,
                    svc=svc,
                    issue=issue,
                    prev_dev_task=dev_task,
                    max_attempts=get_settings().max_dev_retry,
                )

    # 1.5b: 成功路径下触发 review_worker（必须在 DB session 关闭后发，
    # 否则 worker 可能比 commit 还快读到旧状态）
    if enqueue_review:
        celery_app.send_task(
            "app.workers.review_worker.review_dev_task",
            args=[str(issue_id), str(dev_task_id)],
            queue="review_queue",
        )

    # 2.3: 失败重试入队（同样必须 commit 后再发）
    if retry_ctx and retry_ctx.get("action") == "retry":
        celery_app.send_task(
            "app.workers.dev_worker.develop_issue",
            args=[str(issue_id), str(retry_ctx["new_dev_task_id"])],
            queue="dev_queue",
        )
        log.info(
            "dev_worker.retry_enqueued",
            issue_id=str(issue_id),
            new_dev_task_id=str(retry_ctx["new_dev_task_id"]),
            new_attempt=retry_ctx["new_attempt"],
            prev_failure=retry_ctx["prev_failure_reason"],
        )

    log.info(
        "dev_worker.done",
        issue_id=str(issue_id),
        dev_task_id=str(dev_task_id),
        success=success,
        cost=total_cost_usd,
        turns=num_turns,
        review_enqueued=enqueue_review,
        retry_action=(retry_ctx or {}).get("action"),
    )
    return {
        "success": success,
        "total_cost_usd": total_cost_usd,
        "num_turns": num_turns,
        "failure_reason": dev_task.failure_reason if not success else None,
        "retry_enqueued": bool(retry_ctx and retry_ctx.get("action") == "retry"),
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


# ---------------------------------------------------------------------------
# 2.3: 任务级 fallback —— 沙箱内 Claude Code CLI provider 切换
# ---------------------------------------------------------------------------


def build_sandbox_llm_env(
    settings: Any, *, use_fallback: bool,
) -> tuple[dict[str, str], str]:
    """返回 (env_dict, model_label)。

    primary：用 Settings 里的 anthropic_* 配置；CLAUDE_MODEL 默认 sonnet-4-6
    fallback：从 models.yaml.agents.agent_b.fallback 读 base_url / auth_token /
              model，CLI 仅用 auth_token，不带 api_key。

    fallback 配置缺失时 fallback 到 primary（log warning）；不让任务直接死。
    """
    if not use_fallback:
        env: dict[str, str] = {}
        if settings.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key.get_secret_value()
        if settings.anthropic_base_url:
            env["ANTHROPIC_BASE_URL"] = settings.anthropic_base_url
        if settings.anthropic_auth_token:
            env["ANTHROPIC_AUTH_TOKEN"] = settings.anthropic_auth_token.get_secret_value()
        return env, "claude-sonnet-4-6"

    # use_fallback=True：从 models.yaml.agent_b.fallback 拉
    from app.llm.factory import _load_yaml, _env_or

    data = _load_yaml()
    agent_b_cfg = (data.get("agents") or {}).get("agent_b") or {}
    fb_cfg = agent_b_cfg.get("fallback") or {}
    if not fb_cfg.get("enabled"):
        log.warning(
            "dev_worker.fallback_config_missing",
            note="agent_b.fallback.enabled=false; falling back to primary env",
        )
        return build_sandbox_llm_env(settings, use_fallback=False)

    base_url = _env_or(fb_cfg.get("base_url_env")) or fb_cfg.get("base_url") or ""
    auth_token = _env_or(fb_cfg.get("auth_token_env")) or ""
    model = fb_cfg.get("model") or "DeepSeek-V4-Pro"

    if not auth_token or not base_url:
        log.warning(
            "dev_worker.fallback_credentials_missing",
            base_url=bool(base_url), auth_token=bool(auth_token),
        )
        return build_sandbox_llm_env(settings, use_fallback=False)

    return (
        {
            "ANTHROPIC_BASE_URL": base_url,
            "ANTHROPIC_AUTH_TOKEN": auth_token,
            # 不设 ANTHROPIC_API_KEY：CLI 看到 auth_token 时不需要 api_key
        },
        model,
    )


# ---------------------------------------------------------------------------
# 2.3: Agent B 重试机制
# ---------------------------------------------------------------------------


def build_failure_review_context(
    prev_dev_task: DevTask, *, prev_attempt: int,
) -> str:
    """把上一次 dev 失败的原因包成给下次 Agent B 看的 review_context 文本。

    与 review_worker.build_review_context 风格一致；Agent B 既有的
    review_context 字段同时承载 "评审退回" 和 "上次失败" 两类信号。
    """
    reason = prev_dev_task.failure_reason or "unknown"
    detail = (prev_dev_task.failure_detail or "").strip() or "(no detail)"
    lines = [
        f"Previous dev attempt {prev_attempt} failed.",
        f"Failure reason: {reason}",
        "Failure detail:",
        detail[:1500],
        "",
        "Avoid repeating the same approach. If the problem looks unsolvable "
        "(missing external service / fundamentally invalid issue / out of scope), "
        "call report_failure with a clear reason rather than producing a partial fix.",
    ]
    return "\n".join(lines).strip()


async def _enqueue_dev_retry_or_stop(
    *,
    session,
    svc: IssueService,
    issue: Issue,
    prev_dev_task: DevTask,
    max_attempts: int,
) -> dict[str, Any]:
    """决定是否建新 DevTask 重入。

    复用 review_worker.decide_retry_or_archive 的语义：
        attempt < max → "retry"，否则 "terminal"（保持 DEV_FAILED 等用户介入）。

    返回 dict：commit 后由调用方决定是否 send_task。
    """
    # 局部 import 避免循环依赖
    from app.workers.review_worker import decide_retry_or_archive

    attempt = prev_dev_task.attempt_number or 1
    action = decide_retry_or_archive(
        review_attempt=attempt, max_attempts=max_attempts,
    )
    if action != "retry":
        log.info(
            "dev_worker.retry_exhausted",
            issue_id=str(issue.id),
            attempts=attempt,
            max=max_attempts,
        )
        return {"action": "terminal"}

    new_dev_task = DevTask(
        issue_id=issue.id,
        attempt_number=attempt + 1,
        status=DevTaskStatus.PENDING,
        review_context=build_failure_review_context(
            prev_dev_task, prev_attempt=attempt,
        ),
        # 任务级 fallback：dev 整 task 失败 → 下次切到 DeepSeek
        use_fallback_provider=True,
    )
    session.add(new_dev_task)
    await session.flush()

    try:
        # 复用 IssueService.re_queue_dev：DEV_FAILED 已在 QUEUED_DEV 白名单内
        await svc.re_queue_dev(issue)
    except InvalidTransitionError as e:
        log.error(
            "dev_worker.retry_transition_failed",
            issue_id=str(issue.id),
            error=str(e),
        )
        return {"action": "noop"}

    return {
        "action": "retry",
        "new_dev_task_id": new_dev_task.id,
        "new_attempt": new_dev_task.attempt_number,
        "prev_failure_reason": prev_dev_task.failure_reason,
        "use_fallback_provider": True,
    }
