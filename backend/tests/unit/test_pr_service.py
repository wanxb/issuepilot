"""PRService 单元测试（respx mock）。"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.services.pr_service import (
    BranchNotPushedError,
    CreatedPR,
    DuplicatePRError,
    NoDevTokenError,
    PRCreationError,
    PRService,
    GITHUB_API_BASE,
)


@pytest.mark.asyncio
async def test_create_pr_requires_dev_token() -> None:
    svc = PRService(dev_token=None)
    with pytest.raises(NoDevTokenError):
        await svc.create_pr(
            base_repo="o/r", base_branch="main",
            head_repo="dev/r", head_branch="feat",
            title="fix: ...", body="body",
        )


@pytest.mark.asyncio
@respx.mock(base_url=GITHUB_API_BASE)
async def test_create_pr_happy_path(respx_mock: respx.Router) -> None:
    respx_mock.get("/repos/dev/r/branches/feat").mock(
        return_value=Response(200, json={"name": "feat"})
    )
    respx_mock.post("/repos/o/r/pulls").mock(
        return_value=Response(
            201,
            json={
                "number": 42,
                "html_url": "https://github.com/o/r/pull/42",
                "title": "fix: thing",
                "body": "Closes #1",
                "created_at": "2026-06-18T03:00:00Z",
            },
        )
    )
    svc = PRService(dev_token="t-abc")
    pr = await svc.create_pr(
        base_repo="o/r", base_branch="main",
        head_repo="dev/r", head_branch="feat",
        title="fix: thing", body="Closes #1",
    )
    assert isinstance(pr, CreatedPR)
    assert pr.number == 42
    assert pr.url == "https://github.com/o/r/pull/42"
    assert pr.base_branch == "main"

    # 校验 POST body 形如 head=dev:feat
    posted = respx_mock.calls.last.request
    import json
    body = json.loads(posted.content)
    assert body["head"] == "dev:feat"
    assert body["base"] == "main"
    assert body["maintainer_can_modify"] is True


@pytest.mark.asyncio
@respx.mock(base_url=GITHUB_API_BASE)
async def test_create_pr_branch_missing(respx_mock: respx.Router) -> None:
    respx_mock.get("/repos/dev/r/branches/feat").mock(
        return_value=Response(404, json={"message": "Branch not found"})
    )
    svc = PRService(dev_token="t-abc")
    with pytest.raises(BranchNotPushedError):
        await svc.create_pr(
            base_repo="o/r", base_branch="main",
            head_repo="dev/r", head_branch="feat",
            title="t", body="b",
        )


@pytest.mark.asyncio
@respx.mock(base_url=GITHUB_API_BASE)
async def test_create_pr_duplicate(respx_mock: respx.Router) -> None:
    respx_mock.get("/repos/dev/r/branches/feat").mock(
        return_value=Response(200, json={"name": "feat"})
    )
    respx_mock.post("/repos/o/r/pulls").mock(
        return_value=Response(
            422,
            json={
                "message": "Validation Failed",
                "errors": [{
                    "resource": "PullRequest",
                    "code": "custom",
                    "message": "A pull request already exists for dev:feat.",
                }],
            },
        )
    )
    svc = PRService(dev_token="t-abc")
    with pytest.raises(DuplicatePRError):
        await svc.create_pr(
            base_repo="o/r", base_branch="main",
            head_repo="dev/r", head_branch="feat",
            title="t", body="b",
        )


@pytest.mark.asyncio
@respx.mock(base_url=GITHUB_API_BASE)
async def test_create_pr_other_error(respx_mock: respx.Router) -> None:
    respx_mock.get("/repos/dev/r/branches/feat").mock(
        return_value=Response(200, json={"name": "feat"})
    )
    respx_mock.post("/repos/o/r/pulls").mock(
        return_value=Response(500, text="server error"),
    )
    svc = PRService(dev_token="t-abc")
    with pytest.raises(PRCreationError) as exc:
        await svc.create_pr(
            base_repo="o/r", base_branch="main",
            head_repo="dev/r", head_branch="feat",
            title="t", body="b",
        )
    assert exc.value.status_code == 500


@pytest.mark.asyncio
@respx.mock(base_url=GITHUB_API_BASE)
async def test_get_default_branch(respx_mock: respx.Router) -> None:
    respx_mock.get("/repos/o/r").mock(
        return_value=Response(200, json={"default_branch": "trunk"})
    )
    svc = PRService(dev_token="t-abc")
    assert await svc.get_default_branch("o", "r") == "trunk"


@pytest.mark.asyncio
async def test_get_default_branch_no_token_falls_back() -> None:
    svc = PRService(dev_token=None)
    assert await svc.get_default_branch("o", "r") == "main"
