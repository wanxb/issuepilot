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

    # ------- issue URL -------

    async def _handle_issue_url(
        self, parsed: ParsedURL, job: CrawlJob,
    ) -> ManualCrawlOutcome:
        assert parsed.issue_number is not None
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

        new_count, reused_count, skipped = await self._upsert_issues(
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

        new_count, reused_count, skipped = await self._upsert_issues(
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
        return repo

    async def _upsert_issues(
        self,
        repo: Repository,
        gh_issues: list[GHIssue],
        *,
        job_id: uuid.UUID,
        source: IssueSource,
    ) -> tuple[int, int, dict[str, int]]:
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

        # 全部提交后再入 Celery 队列（事务安全：DB commit 在 caller，但 send_task 不依赖 commit）
        if new_issue_ids:
            self._enqueue_analyze(new_issue_ids)
        return new_count, reused_count, skipped

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
