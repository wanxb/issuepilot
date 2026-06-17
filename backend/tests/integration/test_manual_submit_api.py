"""POST /api/v1/crawl-jobs/manual + GET /api/v1/issues 集成测试。

依赖：docker-compose postgres 在跑。
truncate 通过 docker exec psql 避开 asyncio.run + TestClient 双 loop 冲突。
"""
from __future__ import annotations

import os
import subprocess
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


def _truncate_db_via_docker() -> None:
    subprocess.run(
        [
            "docker", "exec", "issuepilot-postgres-1",
            "psql", "-U", "issuepilot", "-d", "issuepilot", "-c",
            "TRUNCATE TABLE llm_call_logs, evaluations, issues, "
            "crawl_jobs, repositories RESTART IDENTITY CASCADE;",
        ],
        check=True, capture_output=True,
    )


@pytest.fixture(autouse=True)
def _no_real_celery(monkeypatch: pytest.MonkeyPatch) -> None:
    """禁用 Celery send_task，避免依赖 worker 进程。"""
    monkeypatch.setattr(
        crawler_mod.CrawlerService,
        "_enqueue_analyze",
        lambda self, ids: None,
    )


def _repo_json(full_name: str = "octocat/demo", open_issues: int = 1) -> dict:
    owner, name = full_name.split("/")
    return {
        "id": 1,
        "name": name,
        "full_name": full_name,
        "owner": {"login": owner, "type": "User"},
        "description": "demo",
        "language": "Python",
        "stargazers_count": 10,
        "open_issues_count": open_issues,
        "pushed_at": "2026-06-01T00:00:00Z",
    }


def _issue_json(number: int = 1) -> dict:
    return {
        "id": 100 + number,
        "number": number,
        "title": f"Issue {number}",
        "body": "body",
        "state": "open",
        "html_url": f"https://github.com/octocat/demo/issues/{number}",
        "user": {"login": "alice", "type": "User"},
        "labels": [{"name": "bug"}],
        "pull_request": None,
        "created_at": "2026-06-01T00:00:00Z",
        "updated_at": "2026-06-02T00:00:00Z",
        "closed_at": None,
    }


@respx.mock
def test_invalid_url_returns_400(client: TestClient) -> None:
    _truncate_db_via_docker()
    resp = client.post("/api/v1/crawl-jobs/manual", json={"url": "not a url"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "INVALID_URL"


@respx.mock
def test_issue_url_happy_path(client: TestClient) -> None:
    _truncate_db_via_docker()
    respx.get(f"{GITHUB_API_BASE}/repos/octocat/demo").mock(
        return_value=httpx.Response(200, json=_repo_json()),
    )
    respx.get(f"{GITHUB_API_BASE}/repos/octocat/demo/issues/42").mock(
        return_value=httpx.Response(200, json=_issue_json(42)),
    )
    resp = client.post(
        "/api/v1/crawl-jobs/manual",
        json={"url": "https://github.com/octocat/demo/issues/42"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "issue"
    assert body["repo_full_name"] == "octocat/demo"
    assert body["issues_enqueued"] == 1
    assert body["issues_reused"] == 0


@respx.mock
def test_issue_url_dedup_reuses(client: TestClient) -> None:
    _truncate_db_via_docker()
    respx.get(f"{GITHUB_API_BASE}/repos/octocat/demo").mock(
        return_value=httpx.Response(200, json=_repo_json()),
    )
    respx.get(f"{GITHUB_API_BASE}/repos/octocat/demo/issues/42").mock(
        return_value=httpx.Response(200, json=_issue_json(42)),
    )
    # 第一次入库
    client.post("/api/v1/crawl-jobs/manual",
                json={"url": "https://github.com/octocat/demo/issues/42"})
    # 第二次同 URL：应该 reuse 不入新行
    resp = client.post("/api/v1/crawl-jobs/manual",
                       json={"url": "https://github.com/octocat/demo/issues/42"})
    body = resp.json()
    assert body["issues_enqueued"] == 0
    assert body["issues_reused"] == 1


@respx.mock
def test_repo_url_within_max_issues(client: TestClient) -> None:
    _truncate_db_via_docker()
    respx.get(f"{GITHUB_API_BASE}/repos/octocat/demo").mock(
        return_value=httpx.Response(200, json=_repo_json(open_issues=3)),
    )
    respx.get(f"{GITHUB_API_BASE}/repos/octocat/demo/issues").mock(
        return_value=httpx.Response(200, json=[_issue_json(n) for n in (1, 2, 3)]),
    )
    resp = client.post(
        "/api/v1/crawl-jobs/manual",
        json={"url": "https://github.com/octocat/demo", "max_issues": 50},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "repo"
    assert body["issues_enqueued"] == 3
    assert body["needs_confirmation"] is False


@respx.mock
def test_repo_url_needs_confirmation(client: TestClient) -> None:
    _truncate_db_via_docker()
    respx.get(f"{GITHUB_API_BASE}/repos/octocat/demo").mock(
        return_value=httpx.Response(200, json=_repo_json(open_issues=200)),
    )
    resp = client.post(
        "/api/v1/crawl-jobs/manual",
        json={"url": "https://github.com/octocat/demo", "max_issues": 50},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["needs_confirmation"] is True
    assert body["open_count_total"] == 200
    assert body["issues_enqueued"] == 0


@respx.mock
def test_repo_not_found_returns_404(client: TestClient) -> None:
    _truncate_db_via_docker()
    respx.get(f"{GITHUB_API_BASE}/repos/missing/repo").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"}),
    )
    resp = client.post(
        "/api/v1/crawl-jobs/manual",
        json={"url": "https://github.com/missing/repo"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "REPO_NOT_FOUND"


@respx.mock
def test_issue_list_endpoint(client: TestClient) -> None:
    _truncate_db_via_docker()
    respx.get(f"{GITHUB_API_BASE}/repos/octocat/demo").mock(
        return_value=httpx.Response(200, json=_repo_json()),
    )
    respx.get(f"{GITHUB_API_BASE}/repos/octocat/demo/issues/7").mock(
        return_value=httpx.Response(200, json=_issue_json(7)),
    )
    assert client.post(
        "/api/v1/crawl-jobs/manual",
        json={"url": "https://github.com/octocat/demo/issues/7"},
    ).status_code == 200

    resp = client.get("/api/v1/issues")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Issue 7"
    assert body["items"][0]["repository"]["full_name"] == "octocat/demo"
    assert body["items"][0]["evaluation"] is None
