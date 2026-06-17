"""POST /api/v1/issues/{id}/decide 集成测试。

复用 manual submit 来 seed Issue，再直接 SQL 把状态推到 PENDING_DECISION
（跳过 worker / LLM）。
"""
from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import app.services.crawler_service as crawler_mod
from app.github.client import GITHUB_API_BASE
from app.main import app

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION_TESTS") != "1",
    reason="set RUN_INTEGRATION_TESTS=1 to run",
)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _truncate_db() -> None:
    subprocess.run(
        ["docker", "exec", "issuepilot-postgres-1", "psql",
         "-U", "issuepilot", "-d", "issuepilot", "-c",
         "TRUNCATE TABLE llm_call_logs, evaluations, issues, "
         "crawl_jobs, repositories RESTART IDENTITY CASCADE;"],
        check=True, capture_output=True,
    )


def _force_status(issue_id: str, status: str) -> None:
    subprocess.run(
        ["docker", "exec", "issuepilot-postgres-1", "psql",
         "-U", "issuepilot", "-d", "issuepilot", "-c",
         f"UPDATE issues SET status='{status}' WHERE id='{issue_id}';"],
        check=True, capture_output=True,
    )


@pytest.fixture(autouse=True)
def _no_real_celery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        crawler_mod.CrawlerService, "_enqueue_analyze",
        lambda self, ids: None,
    )


def _seed_issue(client: TestClient) -> str:
    """通过 manual submit 入库一个 Issue，返回 issue_id。"""
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
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    return items[0]["id"]


@respx.mock
def test_decide_ignore_happy_path(client: TestClient) -> None:
    _truncate_db()
    issue_id = _seed_issue(client)
    _force_status(issue_id, "PENDING_DECISION")

    resp = client.post(
        f"/api/v1/issues/{issue_id}/decide", json={"action": "ignore"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "IGNORED"


@respx.mock
def test_decide_start_dev_happy_path(client: TestClient) -> None:
    _truncate_db()
    issue_id = _seed_issue(client)
    _force_status(issue_id, "PENDING_DECISION")

    resp = client.post(
        f"/api/v1/issues/{issue_id}/decide", json={"action": "start_dev"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "QUEUED_DEV"


@respx.mock
def test_decide_invalid_state_returns_409(client: TestClient) -> None:
    """新 seed 的 Issue 是 DISCOVERED 状态，不能直接 decide。"""
    _truncate_db()
    issue_id = _seed_issue(client)
    # 不改状态，保持 DISCOVERED
    resp = client.post(
        f"/api/v1/issues/{issue_id}/decide", json={"action": "ignore"},
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["detail"]["code"] == "INVALID_STATE_TRANSITION"
    assert body["detail"]["from_state"] == "DISCOVERED"


@respx.mock
def test_decide_unknown_issue_returns_404(client: TestClient) -> None:
    _truncate_db()
    fake_id = uuid.uuid4()
    resp = client.post(
        f"/api/v1/issues/{fake_id}/decide", json={"action": "ignore"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "ISSUE_NOT_FOUND"


@respx.mock
def test_decide_invalid_action_returns_422(client: TestClient) -> None:
    _truncate_db()
    issue_id = _seed_issue(client)
    _force_status(issue_id, "PENDING_DECISION")

    resp = client.post(
        f"/api/v1/issues/{issue_id}/decide", json={"action": "delete"},
    )
    # Pydantic 校验失败，FastAPI 自动返 422
    assert resp.status_code == 422, resp.text
