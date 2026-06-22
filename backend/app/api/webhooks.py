"""GitHub Webhook 端点 —— 接收 PR 生命周期事件。

POST /api/v1/webhooks/github

约束（CLAUDE.md §4）：必须 HMAC-SHA256 验签，不得跳过。

支持的事件（X-GitHub-Event）：
    - pull_request:        action ∈ {closed}    → PRTracker.on_pr_closed
    - pull_request_review: action ∈ {submitted} → PRTracker.on_review_received
    - issue_comment:       action ∈ {created}   仅 PR comment（issue.pull_request 非空）
                                                → PRTracker.on_comment_received
    - ping:                                      → 200 OK（用于 GitHub admin 测试连通）

其他事件 / 其他 action：200 OK + log + skip（GitHub 会因为非 2xx 重试）。

未追踪的 PR（pull_requests 表里不存在）：200 OK + log + skip。
不抛 404，避免泄露我们是否处理过该 PR。
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.database import get_session
from app.services.pr_tracker import PRNotTracked, PRTracker

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


# ---------------------------------------------------------------------------
# HMAC 验签
# ---------------------------------------------------------------------------


def verify_signature(*, secret: str, body: bytes, signature_header: str | None) -> bool:
    """GitHub doc: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries

    signature_header 形如 "sha256=abcdef..."。返回 True 表示验签通过。
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.post("/github")
async def github_webhook(
    request: Request,
    x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
    x_github_delivery: str | None = Header(default=None, alias="X-GitHub-Delivery"),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    body = await request.body()

    settings = get_settings()
    secret = (
        settings.github_webhook_secret.get_secret_value()
        if settings.github_webhook_secret
        else None
    )
    if not secret:
        log.error("webhook.no_secret_configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "WEBHOOK_NOT_CONFIGURED",
                    "message": "GITHUB_WEBHOOK_SECRET not set"},
        )

    if not verify_signature(
        secret=secret, body=body, signature_header=x_hub_signature_256,
    ):
        log.warning("webhook.invalid_signature", delivery=x_github_delivery)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_SIGNATURE",
                    "message": "X-Hub-Signature-256 verification failed"},
        )

    if x_github_event == "ping":
        return {"ok": True, "event": "ping"}

    try:
        payload = await request.json()
    except Exception as e:
        log.warning("webhook.invalid_json", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_JSON", "message": str(e)[:200]},
        ) from e

    tracker = PRTracker(session)
    action = payload.get("action", "")

    if x_github_event == "pull_request":
        result = await _handle_pull_request(tracker, action, payload)
    elif x_github_event == "pull_request_review":
        result = await _handle_pull_request_review(tracker, action, payload)
    elif x_github_event == "issue_comment":
        result = await _handle_issue_comment(tracker, action, payload)
    else:
        log.info(
            "webhook.event_ignored",
            event=x_github_event,
            action=action,
            delivery=x_github_delivery,
        )
        return {"ok": True, "event": x_github_event, "action": action, "handled": False}

    await session.commit()

    # 2.3: webhook 落地后入队待分类的 rejection_reasons（commit 后再发，
    # 否则 worker 可能比 commit 还快读到尚未持久化的行）
    if tracker.pending_classify_ids:
        from app.workers.celery_app import celery_app

        for rid in tracker.pending_classify_ids:
            celery_app.send_task(
                "app.workers.classify_worker.classify_rejection",
                args=[str(rid)],
                queue="classify_queue",
            )
        log.info(
            "webhook.classify_enqueued",
            count=len(tracker.pending_classify_ids),
            delivery=x_github_delivery,
        )

    return {
        "ok": True,
        "event": x_github_event,
        "action": action,
        **result,
        "classify_enqueued": len(tracker.pending_classify_ids),
    }


# ---------------------------------------------------------------------------
# 单事件 handler
# ---------------------------------------------------------------------------


async def _handle_pull_request(
    tracker: PRTracker, action: str, payload: dict[str, Any],
) -> dict[str, Any]:
    if action != "closed":
        return {"handled": False, "reason": "non_closed_action"}

    pr_data = payload.get("pull_request") or {}
    pr_url = str(pr_data.get("html_url") or "")
    if not pr_url:
        return {"handled": False, "reason": "no_pr_url"}

    try:
        pr_row = await tracker.find_by_url(pr_url)
    except PRNotTracked:
        log.info("webhook.pr_not_tracked", pr_url=pr_url)
        return {"handled": False, "reason": "pr_not_tracked"}

    merged = bool(pr_data.get("merged", False))
    sender = (payload.get("sender") or {}).get("login")
    merger = ((pr_data.get("merged_by") or {}).get("login")) or sender
    occurred_at = _parse_dt(
        pr_data.get("merged_at") if merged else pr_data.get("closed_at")
    )

    await tracker.on_pr_closed(
        pr_row,
        merged=merged,
        actor=merger,
        merge_commit_sha=pr_data.get("merge_commit_sha"),
        occurred_at=occurred_at,
        raw_payload={
            "merged": merged,
            "merge_commit_sha": pr_data.get("merge_commit_sha"),
            "closed_at": pr_data.get("closed_at"),
        },
    )
    log.info(
        "webhook.pr_closed",
        pr_url=pr_url,
        merged=merged,
        actor=merger,
    )
    return {"handled": True, "merged": merged}


async def _handle_pull_request_review(
    tracker: PRTracker, action: str, payload: dict[str, Any],
) -> dict[str, Any]:
    if action != "submitted":
        return {"handled": False, "reason": "non_submitted_action"}

    pr_data = payload.get("pull_request") or {}
    review = payload.get("review") or {}
    pr_url = str(pr_data.get("html_url") or "")
    if not pr_url:
        return {"handled": False, "reason": "no_pr_url"}

    try:
        pr_row = await tracker.find_by_url(pr_url)
    except PRNotTracked:
        return {"handled": False, "reason": "pr_not_tracked"}

    reviewer = (review.get("user") or {}).get("login")
    await tracker.on_review_received(
        pr_row,
        reviewer=reviewer,
        state=str(review.get("state") or ""),
        body=review.get("body"),
        occurred_at=_parse_dt(review.get("submitted_at")),
    )
    return {"handled": True, "reviewer": reviewer, "state": review.get("state")}


async def _handle_issue_comment(
    tracker: PRTracker, action: str, payload: dict[str, Any],
) -> dict[str, Any]:
    if action != "created":
        return {"handled": False, "reason": "non_created_action"}

    issue_data = payload.get("issue") or {}
    # 只关心 PR 评论；GitHub 把 PR 也归到 issues，issue.pull_request 非空说明是 PR
    if not issue_data.get("pull_request"):
        return {"handled": False, "reason": "issue_not_pr"}

    pr_url = str(issue_data["pull_request"].get("html_url") or "")
    if not pr_url:
        # fallback：issue.html_url 是 issue 视图链接，转 PR 视图
        # GitHub 实际多数情况下 issue.pull_request.html_url 一定有
        return {"handled": False, "reason": "no_pr_url"}

    try:
        pr_row = await tracker.find_by_url(pr_url)
    except PRNotTracked:
        return {"handled": False, "reason": "pr_not_tracked"}

    comment = payload.get("comment") or {}
    commenter = (comment.get("user") or {}).get("login")
    await tracker.on_comment_received(
        pr_row,
        commenter=commenter,
        body=comment.get("body"),
        occurred_at=_parse_dt(comment.get("created_at")),
    )
    return {"handled": True, "commenter": commenter}


def _parse_dt(s: object) -> datetime:
    if isinstance(s, str):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)
