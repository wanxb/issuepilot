"""profile_worker — Agent D Celery 任务（里程碑 1.5c）。

输入：repo_id（由 CrawlerService 在 upsert 新 repo / profile 过期时触发）。
可选参数：force=True 表示强制重生（即使 profile 未过期），并把
        forced_refresh_count 加 1。

流程：
    1. 加载 Repository；检查 RepoProfile fresh（expires_at > now），
       fresh 且非 force 时直接 skip
    2. 用 GitHubClient 预取：
       - README（README.md / README.rst / readme.md）
       - CONTRIBUTING（CONTRIBUTING.md / .github/CONTRIBUTING.md / docs/CONTRIBUTING.md）
       - 语言对应的 package manifest
       - 测试 workflow（.github/workflows/test.yml / ci.yml）
       - 最近 5 个 merged PR + 各自的 diff 头部
    3. 构造 AgentDInput → AgentD.generate_profile()
    4. upsert RepoProfile（旧记录如存在则删，沿用其 forced_refresh_count）
    5. 写 llm_call_logs

异常：
    - LLMCallError：Celery autoretry 1 次
    - ToolNotCalledError / SchemaValidationError：日志 + 不重试（profile 缺失
      时 Agent B/C 走降级路径，不阻塞主流程）
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from celery import Task
from sqlalchemy import select

from app.agents.agent_d import (
    AgentD,
    SchemaValidationError,
    ToolNotCalledError,
)
from app.agents.schemas import AgentDInput, MergedPRSample
from app.db.database import session_scope
from app.github.client import GitHubClient
from app.llm.base import LLMCallError
from app.llm.factory import build_client
from app.llm.logging import persist_attempts
from app.models.enums import AgentKind, ProfileQuality
from app.models.repo_profile import RepoProfile
from app.models.repository import Repository
from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)

PROFILE_TTL_DAYS = 90

# 备选路径（按优先级尝试）
_README_PATHS = ("README.md", "README.rst", "readme.md", "README")
_CONTRIBUTING_PATHS = (
    "CONTRIBUTING.md",
    ".github/CONTRIBUTING.md",
    "docs/CONTRIBUTING.md",
)
_TEST_WORKFLOW_PATHS = (
    ".github/workflows/test.yml",
    ".github/workflows/test.yaml",
    ".github/workflows/ci.yml",
    ".github/workflows/ci.yaml",
    ".github/workflows/tests.yml",
)

# language -> 优先级 list
_MANIFEST_BY_LANG: dict[str, tuple[str, ...]] = {
    "python": ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"),
    "javascript": ("package.json",),
    "typescript": ("package.json",),
    "go": ("go.mod",),
    "rust": ("Cargo.toml",),
    "java": ("pom.xml", "build.gradle", "build.gradle.kts"),
    "ruby": ("Gemfile", "*.gemspec"),
    "php": ("composer.json",),
}


@celery_app.task(
    name="app.workers.profile_worker.generate_profile",
    bind=True,
    autoretry_for=(LLMCallError,),
    max_retries=1,
    default_retry_delay=10,
    acks_late=True,
)
def generate_profile(self: Task, repo_id: str, force: bool = False) -> dict[str, object]:
    """Celery 同步包装：内部跑 asyncio.run。"""
    return asyncio.run(_generate_profile_async(uuid.UUID(repo_id), force=force))


# ---------------------------------------------------------------------------


async def _generate_profile_async(
    repo_id: uuid.UUID,
    *,
    force: bool = False,
) -> dict[str, object]:
    # ---- Phase 1: 检查 fresh ----
    async with session_scope() as s:
        repo = await s.get(Repository, repo_id)
        if repo is None:
            log.error("profile_worker.repo_missing", repo_id=str(repo_id))
            return {"error": "repo_not_found"}

        existing_stmt = select(RepoProfile).where(RepoProfile.repo_id == repo_id)
        existing = (await s.execute(existing_stmt)).scalar_one_or_none()

        now = datetime.now(timezone.utc)
        is_fresh = existing is not None and existing.expires_at > now
        if is_fresh and not force:
            log.info(
                "profile_worker.skip_fresh",
                repo_id=str(repo_id),
                expires_at=existing.expires_at.isoformat(),
            )
            return {"skipped": True, "reason": "fresh"}

        # 记录 owner/repo + language 以脱离 session
        owner = repo.owner
        name = repo.name
        language = (repo.primary_language or "").lower()
        full_name = repo.full_name
        prior_refresh_count = existing.forced_refresh_count if existing else 0

    # ---- Phase 2: 预取 GitHub 资料（不持 DB 锁）----
    inputs = await _fetch_inputs(owner=owner, name=name, language=language)
    inputs_for_agent = AgentDInput(
        repo_full_name=full_name,
        repo_language=language or None,
        readme_content=inputs["readme"] or "",
        contributing_content=inputs["contributing"],
        package_manifest_path=inputs["manifest_path"],
        package_manifest_content=inputs["manifest_content"],
        test_workflow_content=inputs["test_workflow"],
        merged_pr_samples=inputs["pr_samples"],
    )

    # ---- Phase 3: Agent D 调用 ----
    client = build_client("agent_d")
    try:
        agent = AgentD(client)
        try:
            output, llm_resp = await agent.generate_profile(inputs_for_agent)
        except (ToolNotCalledError, SchemaValidationError) as e:
            log.error(
                "profile_worker.model_output_invalid",
                repo_id=str(repo_id),
                error=str(e)[:300],
            )
            return {"error": "model_output_invalid"}

        # ---- Phase 4: upsert RepoProfile + 写 llm_call_logs ----
        async with session_scope() as s:
            # 重新 fetch 既有行（可能并发被改）
            existing_stmt = select(RepoProfile).where(RepoProfile.repo_id == repo_id)
            existing = (await s.execute(existing_stmt)).scalar_one_or_none()
            if existing is not None:
                refresh_count = prior_refresh_count + (1 if force else 0)
                await s.delete(existing)
                await s.flush()
            else:
                refresh_count = 0

            generated_at = datetime.now(timezone.utc)
            expires_at = generated_at + timedelta(days=PROFILE_TTL_DAYS)

            profile = RepoProfile(
                repo_id=repo_id,
                test_command=output.test_command,
                install_command=output.install_command,
                lint_command=output.lint_command,
                code_style_notes=output.code_style_notes,
                contributing_summary=output.contributing_summary,
                forbidden_patterns=output.forbidden_patterns or None,
                pr_title_convention=output.pr_title_convention,
                merged_pr_examples=[
                    {
                        "url": e.url,
                        "title_pattern": e.title_pattern,
                        "diff_style_note": e.diff_style_note,
                    }
                    for e in output.merged_pr_examples
                ],
                profile_quality=ProfileQuality(output.profile_quality),
                quality_reason=output.quality_reason,
                generated_at=generated_at,
                expires_at=expires_at,
                forced_refresh_count=refresh_count,
                provider=llm_resp.provider,
                model=llm_resp.model,
                prompt_version=agent.prompt_version,
                input_tokens=llm_resp.final_attempt.input_tokens,
                output_tokens=llm_resp.final_attempt.output_tokens,
                cost_usd=sum(a.cost_usd for a in llm_resp.all_attempts),
                is_fallback=llm_resp.is_fallback,
            )
            s.add(profile)
            await s.flush()

            await persist_attempts(
                s,
                llm_resp.all_attempts,
                agent_kind=AgentKind.AGENT_D,
                issue_id=None,
                prompt_version=agent.prompt_version,
            )

            log.info(
                "profile_worker.done",
                repo_id=str(repo_id),
                profile_quality=output.profile_quality,
                cost=sum(a.cost_usd for a in llm_resp.all_attempts),
                provider=llm_resp.provider,
                is_fallback=llm_resp.is_fallback,
            )
            return {
                "profile_quality": output.profile_quality,
                "is_fallback": llm_resp.is_fallback,
            }

    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# GitHub 资料预取
# ---------------------------------------------------------------------------


async def _fetch_inputs(
    *, owner: str, name: str, language: str,
) -> dict[str, object]:
    """串行预取所需的所有外部资料。

    全部失败会返回空字符串，让 Agent D 走 profile_quality=low 兜底，
    不抛异常（profile 缺失时 Agent B/C 自学习降级）。
    """
    result: dict[str, object] = {
        "readme": None,
        "contributing": None,
        "manifest_path": None,
        "manifest_content": None,
        "test_workflow": None,
        "pr_samples": [],
    }

    gh = GitHubClient.from_settings()
    try:
        # README
        result["readme"] = await _try_paths(gh, owner, name, _README_PATHS)
        # CONTRIBUTING
        result["contributing"] = await _try_paths(gh, owner, name, _CONTRIBUTING_PATHS)
        # 测试 workflow
        result["test_workflow"] = await _try_paths(gh, owner, name, _TEST_WORKFLOW_PATHS)
        # 语言 manifest
        manifest_path, manifest_content = await _try_manifest(gh, owner, name, language)
        result["manifest_path"] = manifest_path
        result["manifest_content"] = manifest_content
        # 最近 5 个 merged PR + diff 片段
        try:
            merged_prs = await gh.list_recent_merged_prs(owner, name, count=5)
        except Exception as e:
            log.debug("profile_worker.list_prs_failed", error=str(e))
            merged_prs = []
        samples: list[MergedPRSample] = []
        for pr in merged_prs:
            diff = await gh.get_pr_diff(owner, name, pr.number, max_bytes=4_000)
            samples.append(
                MergedPRSample(
                    url=pr.html_url,
                    title=pr.title,
                    diff_snippet=diff or "",
                )
            )
        result["pr_samples"] = samples
    finally:
        await gh.aclose()
    return result


async def _try_paths(
    gh: GitHubClient, owner: str, name: str, paths: tuple[str, ...],
) -> str | None:
    for p in paths:
        try:
            content = await gh.get_file_content(owner, name, p)
        except Exception as e:
            log.debug("profile_worker.fetch_failed", path=p, error=str(e))
            continue
        if content:
            return content
    return None


async def _try_manifest(
    gh: GitHubClient, owner: str, name: str, language: str,
) -> tuple[str | None, str | None]:
    # 语言对应的优先级路径
    paths = _MANIFEST_BY_LANG.get(language, ())
    # glob pattern（*.gemspec 等）GitHub contents API 不支持，跳过通配项
    for p in paths:
        if "*" in p:
            continue
        try:
            content = await gh.get_file_content(owner, name, p)
        except Exception:
            content = None
        if content:
            return p, content
    return None, None
