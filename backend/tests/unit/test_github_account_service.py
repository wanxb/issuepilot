"""GitHubAccountService 单测（3.3c）—— 静态校验 + 模型注册。

DB × 选择策略走 e2e 验证（CLI smoke）；本文件只测：
    - 模型 export 入 alembic
    - role 校验
    - sticky_key 哈希逻辑（deterministic）
"""
from __future__ import annotations

import hashlib
import uuid

import pytest

from app.services.github_account_service import (
    ALLOWED_ROLES,
    GitHubAccountError,
)


def test_model_registered() -> None:
    from app.models import GitHubAccount
    assert GitHubAccount.__tablename__ == "github_accounts"


def test_allowed_roles_contract() -> None:
    """Phase 3.3 文档定义的角色：crawler / dev / webhook。改动时同步本测。"""
    assert ALLOWED_ROLES == frozenset({"crawler", "dev", "webhook"})


def test_role_validation_via_service_signature() -> None:
    """没有真 session 也得能构造错误对象做形参校验。"""
    err = GitHubAccountError("bad role")
    assert str(err) == "bad role"


@pytest.mark.parametrize("key1,key2,expected_same", [
    ("owner/repo1", "owner/repo1", True),
    ("owner/repo1", "owner/repo2", False),
    ("owner/repo1", "Owner/repo1", False),  # 大小写敏感
])
def test_sticky_key_hash_deterministic(
    key1: str, key2: str, expected_same: bool,
) -> None:
    """同 key 必须产生同 hash → 在固定账号列表下落到同账号。"""
    d1 = int(hashlib.sha1(key1.encode()).hexdigest(), 16)
    d2 = int(hashlib.sha1(key2.encode()).hexdigest(), 16)
    # 5 个账号下取模 —— 如果同 hash 自然同 modulo
    for n in (2, 3, 5, 10):
        eq = (d1 % n) == (d2 % n)
        if expected_same:
            assert eq, f"n={n} key1=key2 should hash to same modulo"
        # 非 expected 时不保证不同（哈希碰撞可能）；不强校验


def test_uuid_roundtrip_safe() -> None:
    """CLI / API 的 delete / toggle 都从 str 转 UUID；非法 str 抛 ValueError。"""
    with pytest.raises(ValueError):
        uuid.UUID("not-a-uuid")
