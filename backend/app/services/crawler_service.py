"""CrawlerService —— GitHub 抓取 + DB 入库 + 任务入队的编排层。

PR #1.2 落地：手动 URL 入口 (POST /crawl-jobs/manual)。
后续：定时 cron 入口（按 crawl_targets 配置）。

约定：
    - 唯一性：(repository_id, github_number) 已建 UNIQUE 约束
    - 复用：dedup 命中（issue 已存在）时不重新评估，直接复用旧 evaluation
    - 入队：写完 issues 后 send_task 到 analyze_queue；任务失败不影响 DB 落地
    - crawl_jobs：每次手动入口建一条，stats JSONB 写入聚合指标
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.github.client import (
    GitHubClient,
    RateLimitedError,
    RepoForbiddenError,
    RepoNotFoundError,
)
from app.github.types import GHIssue, GHRepo
from app.github.url_parser import (
    InvalidGitHubURLError,
    ParsedURL,
    parse_github_url,
)
from app.models.crawl_job import CrawlJob
from app.models.enums import CrawlStatus, CrawlTrigger, IssueSource, IssueStatus
from app.models.issue import Issue
from app.models.repository import Repository

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# 外部错误（API 层捕获并转 HTTP code）
# ---------------------------------------------------------------------------


class CrawlerError(Exception):
    code: str = "CRAWLER_ERROR"


class InvalidUrlError(CrawlerError):
    code = "INVALID_URL"


class RepoNotFound(CrawlerError):
    code = "REPO_NOT_FOUND"


class RepoForbidden(CrawlerError):
    code = "REPO_PRIVATE"


class RateLimited(CrawlerError):
    code = "RATE_LIMITED"

    def __init__(self, message: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class Blacklisted(CrawlerError):
    code = "BLACKLISTED"

    def __init__(self, message: str, *, pattern: str) -> None:
        super().__init__(message)
        self.pattern = pattern


class ConfirmationRequired(CrawlerError):
    code = "CONFIRMATION_REQUIRED"

    def __init__(
        self,
        message: str,
        *,
        open_count_total: int,
        max_issues: int,
        repo_full_name: str,
    ) -> None:
        super().__init__(message)
        self.open_count_total = open_count_total
        self.max_issues = max_issues
        self.repo_full_name = repo_full_name


# ---------------------------------------------------------------------------
# 返回结构
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ManualCrawlOutcome:
    job_id: uuid.UUID
    mode: str
    repo_full_name: str
    issues_enqueued: int = 0
    issues_reused: int = 0
    issues_skipped_reason: dict[str, int] = field(default_factory=dict)
    needs_confirmation: bool = False
    open_count_total: int | None = None


@dataclass(slots=True)
class TargetCrawlOutcome:
    """2.2: 一次 scheduled_crawl 的聚合结果（一个 target → 多 repo）。"""
    job_id: uuid.UUID
    target_id: uuid.UUID
    repos_attempted: int = 0
    repos_succeeded: int = 0
    repos_failed: int = 0
    issues_enqueued: int = 0
    issues_reused: int = 0
    failures: dict[str, str] = field(default_factory=dict)  # repo -> error
    # 待 commit 后由 caller 入队的 issue 列表，避免 analyze_worker 比 commit 更快
    pending_analyze_ids: list[uuid.UUID] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class CrawlerService:
    """无状态服务：每次操作传入 session + github client。"""

    def __init__(self, session: AsyncSession, github: GitHubClient) -> None:
        self._s = session
        self._gh = github

    async def from_manual_url(
        self,
        *,
        raw_url: str,
        max_issues: int = 50,
        force_confirm: bool = False,
    ) -> ManualCrawlOutcome:
        """看板手动 URL 入口主流程。"""
        # 1. URL 解析
        try:
            parsed = parse_github_url(raw_url)
        except InvalidGitHubURLError as e:
            raise InvalidUrlError(str(e)) from e

        # 2. crawl_jobs 行
        job = CrawlJob(
            trigger=CrawlTrigger.MANUAL_URL,
            status=CrawlStatus.RUNNING,
            input_url=raw_url,
            started_at=datetime.now(timezone.utc),
            stats={},
        )
        self._s.add(job)
        await self._s.flush()

        t0 = time.perf_counter()
        try:
            if parsed.mode == "issue":
                outcome = await self._handle_issue_url(parsed, job)
            else:
                outcome = await self._handle_repo_url(parsed, job, max_issues, force_confirm)
        except CrawlerError as e:
            job.status = CrawlStatus.FAILED
            job.finished_at = datetime.now(timezone.utc)
            job.stats = {**(job.stats or {}), "error": str(e), "error_code": e.code}
            raise
        except Exception as e:
            job.status = CrawlStatus.FAILED
            job.finished_at = datetime.now(timezone.utc)
            job.stats = {**(job.stats or {}), "error": str(e)}
            raise

        job.status = CrawlStatus.SUCCEEDED if not outcome.needs_confirmation else CrawlStatus.PARTIAL
        job.finished_at = datetime.now(timezone.utc)
        elapsed = round(time.perf_counter() - t0, 2)
        job.stats = {
            **(job.stats or {}),
            "duration_seconds": elapsed,
            "mode": parsed.mode,
            "issues_enqueued": outcome.issues_enqueued,
            "issues_reused": outcome.issues_reused,
            "issues_skipped_reason": outcome.issues_skipped_reason,
        }
        return outcome

    # ------- 2.2 定时抓取入口 -------

    async def from_target(
        self,
        *,
        target_id: uuid.UUID,
        target_name: str,
        source: str,
        spec: dict,
        max_issues_per_repo: int = 20,
    ) -> "TargetCrawlOutcome":
        """按 crawl_target 的 source + spec 拉 repo 列表，逐 repo 抓 issues。

        策略：
            - 每个 target run = 1 个 crawl_job（trigger=CRON），stats 聚合
              所有 repos 的指标
            - 单 repo 抓取失败不中断整个 target（log + 记 failures dict）
            - max_issues_per_repo 限制防止 trending 头部仓库刷爆 analyze_queue
        """
        from app.services.crawl_sources import fetch_repos

        job = CrawlJob(
            trigger=CrawlTrigger.CRON,
            status=CrawlStatus.RUNNING,
            input_url=None,
            started_at=datetime.now(timezone.utc),
            stats={
                "target_id": str(target_id),
                "target_name": target_name,
                "source": source,
                "spec": spec,
            },
        )
        self._s.add(job)
        await self._s.flush()

        t0 = time.perf_counter()
        outcome = TargetCrawlOutcome(job_id=job.id, target_id=target_id)

        try:
            repo_full_names = await fetch_repos(source, spec)
        except Exception as e:
            job.status = CrawlStatus.FAILED
            job.finished_at = datetime.now(timezone.utc)
            job.stats = {**(job.stats or {}), "error": str(e)[:300]}
            log.error("crawler.target_fetch_failed",
                      target_id=str(target_id), error=str(e))
            raise

        outcome.repos_attempted = len(repo_full_names)
        # 3.3: 黑名单一次性预加载，避免每个 repo 都查 DB
        from app.services.blacklist_service import BlacklistService

        bl = BlacklistService(self._s)
        repo_bl = await bl.list_enabled(entity_type="repo")

        # 4.x: 领域白名单（spec.domains 非空时启用后置过滤）
        accepted_domains: list[str] = spec.get("domains") or []
        domain_enabled = bool(accepted_domains)

        for full_name in repo_full_names:
            try:
                owner, name = full_name.split("/", 1)
            except ValueError:
                outcome.failures[full_name] = "invalid_full_name"
                continue
            # 3.3: 检黑名单
            from app.services.blacklist_service import _match
            hit = next((r for r in repo_bl if _match(r.pattern, full_name)), None)
            if hit is not None:
                outcome.failures[full_name] = f"blacklisted:{hit.pattern}"
                outcome.repos_failed += 1
                continue
            try:
                gh_repo = await self._gh.get_repo(owner, name)
            except (RepoNotFoundError, RepoForbiddenError) as e:
                outcome.failures[full_name] = type(e).__name__
                outcome.repos_failed += 1
                continue
            except RateLimitedError as e:
                # 限流直接终止：不浪费 GitHub 配额，记录后让下次 cron 重来
                outcome.failures[full_name] = (
                    f"rate_limited(retry_after={e.retry_after_seconds})"
                )
                break

            # 4.x: 领域白名单后置过滤
            if domain_enabled:
                from app.services.crawl_domain import match_repo
                dm = match_repo(
                    name=gh_repo.name,
                    description=gh_repo.description,
                    topics=getattr(gh_repo, "topics", None) or [],
                    accepted_domains=accepted_domains,
                )
                if dm.matched_domain is None:
                    outcome.failures[full_name] = (
                        f"off_topic(not in {accepted_domains})"
                    )
                    outcome.repos_failed += 1
                    continue
                log.info(
                    "crawler.domain_matched",
                    repo=full_name,
                    domain=dm.matched_domain, signal=dm.signal,
                )

            try:
                repo = await self._upsert_repo(gh_repo)
                gh_issues = await self._gh.list_open_issues(
                    owner, name, max_count=max_issues_per_repo,
                )
                new_c, reused_c, _skipped, new_ids = await self._upsert_issues(
                    repo, gh_issues,
                    job_id=job.id, source=IssueSource.CRAWL,
                    enqueue_now=False,            # 2.2: 延迟到 commit 后
                )
                outcome.issues_enqueued += new_c
                outcome.issues_reused += reused_c
                outcome.repos_succeeded += 1
                outcome.pending_analyze_ids.extend(new_ids)
            except Exception as e:
                outcome.failures[full_name] = str(e)[:200]
                outcome.repos_failed += 1
                log.warning("crawler.target_repo_failed",
                            repo=full_name, error=str(e))

        elapsed = round(time.perf_counter() - t0, 2)
        # 状态：全失败 → FAILED；有 failure 但 succeeded>0 → PARTIAL；否则 SUCCEEDED
        if outcome.repos_succeeded == 0 and outcome.repos_attempted > 0:
            job.status = CrawlStatus.FAILED
        elif outcome.failures:
            job.status = CrawlStatus.PARTIAL
        else:
            job.status = CrawlStatus.SUCCEEDED
        job.finished_at = datetime.now(timezone.utc)
        job.stats = {
            **(job.stats or {}),
            "duration_seconds": elapsed,
            "repos_attempted": outcome.repos_attempted,
            "repos_succeeded": outcome.repos_succeeded,
            "repos_failed": outcome.repos_failed,
            "issues_enqueued": outcome.issues_enqueued,
            "issues_reused": outcome.issues_reused,
            "failures": outcome.failures,
        }
        log.info("crawler.target_done",
                 target_id=str(target_id), **{
                     k: v for k, v in {
                         "succeeded": outcome.repos_succeeded,
                         "failed": outcome.repos_failed,
                         "enqueued": outcome.issues_enqueued,
                         "duration_s": elapsed,
                     }.items()
                 })
        return outcome

    # ------- issue URL -------

    async def _handle_issue_url(
        self, parsed: ParsedURL, job: CrawlJob,
    ) -> ManualCrawlOutcome:
        assert parsed.issue_number is not None

        # 3.3: 黑名单前置（避免对已知毒源做 API 调用）
        from app.services.blacklist_service import BlacklistService

        bl = BlacklistService(self._s)
        full = f"{parsed.owner}/{parsed.repo}"
        blocked, pat = await bl.is_issue_blacklisted(full, parsed.issue_number)
        if blocked:
            raise Blacklisted(
                f"{full}#{parsed.issue_number} matches blacklist pattern {pat!r}",
                pattern=pat or "",
            )

        gh_repo = await self._fetch_repo(parsed)
        repo = await self._upsert_repo(gh_repo)

        try:
            gh_issue = await self._gh.get_issue(
                parsed.owner, parsed.repo, parsed.issue_number,
            )
        except RepoNotFoundError as e:
            raise RepoNotFound(str(e)) from e
        except RateLimitedError as e:
            raise RateLimited(str(e), retry_after_seconds=e.retry_after_seconds) from e

        new_count, reused_count, skipped, _new_ids = await self._upsert_issues(
            repo, [gh_issue], job_id=job.id, source=IssueSource.MANUAL,
        )
        return ManualCrawlOutcome(
            job_id=job.id,
            mode="issue",
            repo_full_name=repo.full_name,
            issues_enqueued=new_count,
            issues_reused=reused_count,
            issues_skipped_reason=skipped,
        )

    # ------- repo URL -------

    async def _handle_repo_url(
        self,
        parsed: ParsedURL,
        job: CrawlJob,
        max_issues: int,
        force_confirm: bool,
    ) -> ManualCrawlOutcome:
        # 3.3: 黑名单前置
        from app.services.blacklist_service import BlacklistService

        bl = BlacklistService(self._s)
        full = f"{parsed.owner}/{parsed.repo}"
        blocked, pat = await bl.is_repo_blacklisted(full)
        if blocked:
            raise Blacklisted(
                f"{full} matches blacklist pattern {pat!r}",
                pattern=pat or "",
            )

        gh_repo = await self._fetch_repo(parsed)
        repo = await self._upsert_repo(gh_repo)

        open_count_total = gh_repo.open_issues_count
        if open_count_total > max_issues and not force_confirm:
            return ManualCrawlOutcome(
                job_id=job.id,
                mode="repo",
                repo_full_name=repo.full_name,
                needs_confirmation=True,
                open_count_total=open_count_total,
            )

        try:
            gh_issues = await self._gh.list_open_issues(
                parsed.owner, parsed.repo, max_count=max_issues,
            )
        except RateLimitedError as e:
            raise RateLimited(str(e), retry_after_seconds=e.retry_after_seconds) from e

        new_count, reused_count, skipped, _new_ids = await self._upsert_issues(
            repo, gh_issues, job_id=job.id, source=IssueSource.MANUAL,
        )
        return ManualCrawlOutcome(
            job_id=job.id,
            mode="repo",
            repo_full_name=repo.full_name,
            issues_enqueued=new_count,
            issues_reused=reused_count,
            issues_skipped_reason=skipped,
            open_count_total=open_count_total,
        )

    # ------- 共用 -------

    async def _fetch_repo(self, parsed: ParsedURL) -> GHRepo:
        try:
            return await self._gh.get_repo(parsed.owner, parsed.repo)
        except RepoNotFoundError as e:
            raise RepoNotFound(str(e)) from e
        except RepoForbiddenError as e:
            raise RepoForbidden(str(e)) from e
        except RateLimitedError as e:
            raise RateLimited(str(e), retry_after_seconds=e.retry_after_seconds) from e

    async def _upsert_repo(self, gh: GHRepo) -> Repository:
        result = await self._s.execute(
            select(Repository).where(Repository.full_name == gh.full_name),
        )
        repo = result.scalar_one_or_none()
        is_new = False
        if repo is None:
            repo = Repository(
                full_name=gh.full_name,
                owner=gh.owner.login,
                name=gh.name,
                github_id=gh.id,
                description=gh.description,
                primary_language=gh.language,
                topics=list(gh.topics) or None,
                stars=gh.stargazers_count,
                forks=gh.forks_count,
                open_issues_count=gh.open_issues_count,
                last_commit_at=gh.pushed_at,
                last_crawled_at=datetime.now(timezone.utc),
            )
            self._s.add(repo)
            is_new = True
        else:
            repo.description = gh.description
            repo.primary_language = gh.language
            repo.topics = list(gh.topics) or None
            repo.stars = gh.stargazers_count
            repo.forks = gh.forks_count
            repo.open_issues_count = gh.open_issues_count
            repo.last_commit_at = gh.pushed_at
            repo.last_crawled_at = datetime.now(timezone.utc)
        await self._s.flush()

        # 1.5c: 缺失 / 过期的 profile 异步入队（不阻塞主流程）
        await self._maybe_enqueue_profile(repo, is_new=is_new)
        return repo

    async def _maybe_enqueue_profile(
        self,
        repo: Repository,
        *,
        is_new: bool,
    ) -> None:
        """RepoProfile 缺失 / 过期 → 入 profile_queue。

        新 repo 总是入队；老 repo 仅当 profile 缺失或过期时入。
        失败仅记日志，不阻塞 crawl。
        """
        from app.models.repo_profile import RepoProfile  # 延迟 import 避免循环

        existing = await self._s.execute(
            select(RepoProfile).where(RepoProfile.repo_id == repo.id),
        )
        profile = existing.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        needs_profile = (
            profile is None
            or profile.expires_at <= now
        )
        if not (is_new or needs_profile):
            return

        from app.workers.celery_app import celery_app
        try:
            celery_app.send_task(
                "app.workers.profile_worker.generate_profile",
                args=[str(repo.id)],
                kwargs={"force": False},
                queue="profile_queue",
            )
            log.info(
                "crawler.profile_enqueued",
                repo_id=str(repo.id),
                full_name=repo.full_name,
                reason="new" if is_new else ("expired" if profile else "missing"),
            )
        except Exception as e:
            log.error(
                "crawler.profile_enqueue_failed",
                repo_id=str(repo.id),
                error=str(e),
            )

    async def _upsert_issues(
        self,
        repo: Repository,
        gh_issues: list[GHIssue],
        *,
        job_id: uuid.UUID,
        source: IssueSource,
        enqueue_now: bool = True,
    ) -> tuple[int, int, dict[str, int], list[uuid.UUID]]:
        skipped: dict[str, int] = {}
        new_count = 0
        reused_count = 0
        new_issue_ids: list[uuid.UUID] = []

        for gh_issue in gh_issues:
            if gh_issue.is_pull_request:
                skipped["is_pull_request"] = skipped.get("is_pull_request", 0) + 1
                continue
            existing = await self._s.execute(
                select(Issue).where(
                    Issue.repository_id == repo.id,
                    Issue.github_number == gh_issue.number,
                ),
            )
            issue = existing.scalar_one_or_none()
            if issue is not None:
                reused_count += 1
                continue
            issue = Issue(
                repository_id=repo.id,
                github_number=gh_issue.number,
                github_id=gh_issue.id,
                github_url=gh_issue.html_url,
                title=gh_issue.title,
                body=gh_issue.body,
                labels=gh_issue.label_names or None,
                author=gh_issue.user.login if gh_issue.user else None,
                status=IssueStatus.DISCOVERED,
                source=source,
                crawl_job_id=job_id,
            )
            self._s.add(issue)
            await self._s.flush()
            new_issue_ids.append(issue.id)
            new_count += 1

        # 2.2 修正：enqueue_now=True（manual URL 路径，小批量，commit 快）保留旧行为；
        # 2.2 from_target 路径设 False，让 caller 在 commit 后批量 enqueue 避免 race
        if new_issue_ids and enqueue_now:
            self._enqueue_analyze(new_issue_ids)
        return new_count, reused_count, skipped, new_issue_ids

    def _enqueue_analyze(self, issue_ids: list[uuid.UUID]) -> None:
        # 延迟 import：避免 worker 启动时循环 import
        from app.workers.celery_app import celery_app

        for iid in issue_ids:
            try:
                celery_app.send_task(
                    "app.workers.analyze_worker.analyze_issue",
                    args=[str(iid)],
                    queue="analyze_queue",
                )
            except Exception as e:
                log.error("crawler.enqueue_failed", issue_id=str(iid), error=str(e))
