"""POST /api/v1/webhooks/github 集成测试。

依赖：docker-compose postgres 在跑（同 manual_submit / decide 测试）。
通过 force_status + 直接插入 pull_requests 行来 seed 数据。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import uuid
from collections.abc import Iterator

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import app.services.crawler_service as crawler_mod
from app.core.config import get_settings
from app.github.client import GITHUB_API_BASE
from app.main import app

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION_TESTS") != "1",
    reason="set RUN_INTEGRATION_TESTS=1 to run",
)


SECRET = "webhook-test-secret"


@pytest.fixture
def client() -> Iterator[TestClient]:
    # 注入 webhook secret 到 Settings cache
    settings = get_settings()
    from pydantic import SecretStr
    object.__setattr__(settings, "github_webhook_secret", SecretStr(SECRET))
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _no_real_celery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        crawler_mod.CrawlerService, "_enqueue_analyze",
        lambda self, ids: None,
    )


def _truncate_db() -> None:
    subprocess.run(
        ["docker", "exec", "issuepilot-postgres-1", "psql",
         "-U", "issuepilot", "-d", "issuepilot", "-c",
         "TRUNCATE TABLE llm_call_logs, pr_outcomes, rejection_reasons, "
         "pull_requests, review_tasks, dev_logs, dev_tasks, "
         "evaluations, issues, crawl_jobs, repo_profiles, repositories "
         "RESTART IDENTITY CASCADE;"],
        check=True, capture_output=True,
    )


def _force_status(issue_id: str, status: str) -> None:
    subprocess.run(
        ["docker", "exec", "issuepilot-postgres-1", "psql",
         "-U", "issuepilot", "-d", "issuepilot", "-c",
         f"UPDATE issues SET status='{status}' WHERE id='{issue_id}';"],
        check=True, capture_output=True,
    )


def _insert_pr(issue_id: str, pr_url: str) -> str:
    """直接 SQL 插入一行 pull_requests，返回 pr_id。"""
    pr_id = str(uuid.uuid4())
    sql = (
        f"INSERT INTO pull_requests "
        f"(id, issue_id, github_pr_number, github_pr_url, title, body, "
        f"head_repo, head_branch, base_repo, base_branch, status, "
        f"submitted_at, created_at, updated_at) "
        f"VALUES ('{pr_id}', '{issue_id}', 1, '{pr_url}', 't', 'b', "
        f"'dev/r', 'feat', 'o/r', 'main', 'OPEN', "
        f"now(), now(), now());"
    )
    subprocess.run(
        ["docker", "exec", "issuepilot-postgres-1", "psql",
         "-U", "issuepilot", "-d", "issuepilot", "-c", sql],
        check=True, capture_output=True,
    )
    return pr_id


def _seed_issue(client: TestClient) -> str:
    respx.get(f"{GITHUB_API_BASE}/repos/o/r").mock(
        return_value=httpx.Response(200, json={
            "id": 1, "name": "r", "full_name": "o/r",
            "owner": {"login": "o"}, "stargazers_count": 1, "open_issues_count": 1,
        }),
    )
    respx.get(f"{GITHUB_API_BASE}/repos/o/r/issues/1").mock(
        return_value=httpx.Response(200, json={
            "id": 1, "number": 1, "title": "x", "body": "y",
            "state": "open", "html_url": "https://github.com/o/r/issues/1",
            "labels": [], "pull_request": None,
            "created_at": "2026-06-01T00:00:00Z",
            "updated_at": "2026-06-01T00:00:00Z",
        }),
    )
    submit = client.post(
        "/api/v1/crawl-jobs/manual",
        json={"url": "https://github.com/o/r/issues/1"},
    )
    assert submit.status_code == 200
    listing = client.get("/api/v1/issues")
    items = listing.json()["items"]
    assert items
    return items[0]["id"]


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _post_webhook(
    client: TestClient, *, event: str, payload: dict, signed: bool = True,
) -> httpx.Response:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": "test-delivery",
    }
    if signed:
        headers["X-Hub-Signature-256"] = _sign(body)
    else:
        headers["X-Hub-Signature-256"] = "sha256=deadbeef"
    return client.post("/api/v1/webhooks/github", content=body, headers=headers)


@respx.mock
def test_invalid_signature_returns_401(client: TestClient) -> None:
    _truncate_db()
    resp = _post_webhook(client, event="ping", payload={"zen": "..."}, signed=False)
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "INVALID_SIGNATURE"


@respx.mock
def test_ping_event_ok(client: TestClient) -> None:
    _truncate_db()
    resp = _post_webhook(client, event="ping", payload={"zen": "Keep it simple."})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "event": "ping"}


@respx.mock
def test_pull_request_closed_merged_transitions_to_pr_merged(
    client: TestClient,
) -> None:
    _truncate_db()
    issue_id = _seed_issue(client)
    _force_status(issue_id, "PR_SUBMITTED")
    pr_url = "https://github.com/o/r/pull/1"
    _insert_pr(issue_id, pr_url)

    payload = {
        "action": "closed",
        "pull_request": {
            "html_url": pr_url,
            "merged": True,
            "merge_commit_sha": "deadbeefcafe",
            "merged_at": "2026-06-18T05:00:00Z",
            "closed_at": "2026-06-18T05:00:00Z",
            "merged_by": {"login": "alice"},
        },
        "sender": {"login": "alice"},
    }
    resp = _post_webhook(client, event="pull_request", payload=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["handled"] is True
    assert body["merged"] is True

    # Issue 应已转 PR_MERGED
    listing = client.get("/api/v1/issues").json()
    item = next(i for i in listing["items"] if i["id"] == issue_id)
    assert item["status"] == "PR_MERGED"
    assert item["pull_request"]["final_outcome"] == "MERGED_CLEAN"


@respx.mock
def test_pull_request_closed_unmerged_transitions_to_pr_closed(
    client: TestClient,
) -> None:
    _truncate_db()
    issue_id = _seed_issue(client)
    _force_status(issue_id, "PR_SUBMITTED")
    pr_url = "https://github.com/o/r/pull/2"
    _insert_pr(issue_id, pr_url)

    payload = {
        "action": "closed",
        "pull_request": {
            "html_url": pr_url,
            "merged": False,
            "closed_at": "2026-06-18T05:30:00Z",
        },
        "sender": {"login": "bob"},
    }
    resp = _post_webhook(client, event="pull_request", payload=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["handled"] is True
    assert body["merged"] is False

    listing = client.get("/api/v1/issues").json()
    item = next(i for i in listing["items"] if i["id"] == issue_id)
    assert item["status"] == "PR_CLOSED"
    assert item["pull_request"]["final_outcome"] == "CLOSED_BY_MAINTAINER"


@respx.mock
def test_pull_request_untracked_returns_200_skip(client: TestClient) -> None:
    _truncate_db()
    payload = {
        "action": "closed",
        "pull_request": {
            "html_url": "https://github.com/o/r/pull/9999",
            "merged": True,
        },
        "sender": {"login": "x"},
    }
    resp = _post_webhook(client, event="pull_request", payload=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["handled"] is False
    assert body["reason"] == "pr_not_tracked"


@respx.mock
def test_pull_request_review_recorded(client: TestClient) -> None:
    _truncate_db()
    issue_id = _seed_issue(client)
    _force_status(issue_id, "PR_SUBMITTED")
    pr_url = "https://github.com/o/r/pull/3"
    _insert_pr(issue_id, pr_url)

    payload = {
        "action": "submitted",
        "pull_request": {"html_url": pr_url},
        "review": {
            "state": "changes_requested",
            "body": "Please update tests",
            "submitted_at": "2026-06-18T06:00:00Z",
            "user": {"login": "carol"},
        },
        "sender": {"login": "carol"},
    }
    resp = _post_webhook(client, event="pull_request_review", payload=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["handled"] is True
    assert body["reviewer"] == "carol"


@respx.mock
def test_issue_comment_only_handles_pr_comments(client: TestClient) -> None:
    _truncate_db()
    issue_id = _seed_issue(client)
    _force_status(issue_id, "PR_SUBMITTED")
    pr_url = "https://github.com/o/r/pull/4"
    _insert_pr(issue_id, pr_url)

    # 真正的 PR comment
    pr_comment = {
        "action": "created",
        "issue": {
            "pull_request": {"html_url": pr_url},
        },
        "comment": {
            "body": "Looks great!",
            "created_at": "2026-06-18T06:30:00Z",
            "user": {"login": "dave"},
        },
        "sender": {"login": "dave"},
    }
    resp = _post_webhook(client, event="issue_comment", payload=pr_comment)
    assert resp.status_code == 200
    assert resp.json()["handled"] is True

    # 非 PR 评论（普通 issue）
    non_pr = {**pr_comment, "issue": {"pull_request": None}}
    resp2 = _post_webhook(client, event="issue_comment", payload=non_pr)
    assert resp2.status_code == 200
    assert resp2.json()["handled"] is False
