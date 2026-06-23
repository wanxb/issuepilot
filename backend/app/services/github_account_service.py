"""GitHubAccountService（3.3）—— 账号池 + 选择策略。

选择策略：
    - role 过滤后取 enabled=true 的账号
    - 按 last_used_at ASC（最久没用的）轮询，写入新 last_used_at
    - sticky-by-repo（可选）：同 repo 优先复用上次使用的账号，避免一会儿
      A 一会儿 B 引起 fork 重复 / push 权限错乱

回退：池中无账号 → 返回 None，调用方自然回落到 Settings.github_token /
github_dev_token（保持向后兼容）。
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.github_account import GitHubAccount

log = structlog.get_logger(__name__)


# 允许的 role 值
ALLOWED_ROLES: frozenset[str] = frozenset({
    "crawler",       # 仅 read，用于 CrawlerService 抓 issue/repo
    "dev",           # fork + push + 创建 PR，PRService 用
    "webhook",       # 仅 webhook 验签（短期内只有一种 secret，但保留 role）
})


class GitHubAccountError(Exception):
    pass


class GitHubAccountService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ---- CRUD ----

    async def add(
        self, *, name: str, role: str, token: str,
        username: str | None = None, notes: str | None = None,
        enabled: bool = True,
    ) -> GitHubAccount:
        if role not in ALLOWED_ROLES:
            raise GitHubAccountError(
                f"role must be one of {ALLOWED_ROLES}, got {role!r}",
            )
        if not token or len(token) < 8:
            raise GitHubAccountError("token must be at least 8 chars")
        row = GitHubAccount(
            name=name, role=role, token=token, username=username,
            notes=notes, enabled=enabled,
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def list_all(self) -> list[GitHubAccount]:
        return list((await self._s.execute(
            select(GitHubAccount).order_by(GitHubAccount.created_at)
        )).scalars().all())

    async def list_enabled(self, *, role: str | None = None) -> list[GitHubAccount]:
        stmt = select(GitHubAccount).where(GitHubAccount.enabled.is_(True))
        if role:
            stmt = stmt.where(GitHubAccount.role == role)
        return list((await self._s.execute(stmt)).scalars().all())

    async def delete(self, account_id: str) -> bool:
        row = await self._s.get(GitHubAccount, uuid.UUID(account_id))
        if row is None:
            return False
        await self._s.delete(row)
        await self._s.flush()
        return True

    async def set_enabled(self, account_id: str, *, enabled: bool) -> Optional[GitHubAccount]:
        row = await self._s.get(GitHubAccount, uuid.UUID(account_id))
        if row is None:
            return None
        row.enabled = enabled
        await self._s.flush()
        return row

    # ---- 选择 ----

    async def pick(
        self, *, role: str, sticky_key: str | None = None,
    ) -> GitHubAccount | None:
        """挑一个 enabled 账号。

        sticky_key：repo full_name 等；用其哈希在账号列表里取模，保证同一
        repo 总落到同账号上（避免反复 fork 引起 GitHub 端冲突）。
        """
        accounts = await self.list_enabled(role=role)
        if not accounts:
            return None

        if sticky_key:
            digest = int(hashlib.sha1(sticky_key.encode()).hexdigest(), 16)
            chosen = accounts[digest % len(accounts)]
        else:
            # 最久没用的优先（LRU）
            chosen = min(
                accounts,
                key=lambda a: a.last_used_at or datetime.fromtimestamp(0, tz=timezone.utc),
            )

        chosen.last_used_at = datetime.now(timezone.utc)
        await self._s.flush()
        return chosen
