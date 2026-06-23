"""BlacklistService（3.3）—— pattern 匹配 + CRUD。

pattern 语法：
    repo:
      "owner/repo"          精确匹配
      "owner/*"             owner 下所有 repo
      "*/repo-name"         任意 owner 同名 repo（少用）
    issue:
      "owner/repo#42"       单 issue
      "owner/repo#*"        所有该 repo issue（等价 repo 黑）
"""
from __future__ import annotations

import fnmatch
from typing import Iterable

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.blacklist import Blacklist

log = structlog.get_logger(__name__)


class BlacklistService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ---- CRUD ----

    async def add(
        self, *, entity_type: str, pattern: str,
        reason: str | None = None, enabled: bool = True,
    ) -> Blacklist:
        if entity_type not in ("repo", "issue"):
            raise ValueError(f"entity_type must be repo|issue, got {entity_type!r}")
        if not pattern or len(pattern) > 200:
            raise ValueError("pattern must be 1..200 chars")
        if entity_type == "repo" and "#" in pattern:
            raise ValueError(f"repo pattern must not contain '#': {pattern!r}")
        if entity_type == "issue" and "#" not in pattern:
            raise ValueError(f"issue pattern must contain '#': {pattern!r}")
        row = Blacklist(
            entity_type=entity_type, pattern=pattern,
            reason=reason, enabled=enabled,
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def list_all(self) -> list[Blacklist]:
        rows = (await self._s.execute(
            select(Blacklist).order_by(Blacklist.created_at)
        )).scalars().all()
        return list(rows)

    async def list_enabled(self, *, entity_type: str | None = None) -> list[Blacklist]:
        stmt = select(Blacklist).where(Blacklist.enabled.is_(True))
        if entity_type:
            stmt = stmt.where(Blacklist.entity_type == entity_type)
        rows = (await self._s.execute(stmt)).scalars().all()
        return list(rows)

    async def set_enabled(self, blacklist_id: str, *, enabled: bool) -> Blacklist | None:
        import uuid
        row = await self._s.get(Blacklist, uuid.UUID(blacklist_id))
        if row is None:
            return None
        row.enabled = enabled
        await self._s.flush()
        return row

    async def delete(self, blacklist_id: str) -> bool:
        import uuid
        row = await self._s.get(Blacklist, uuid.UUID(blacklist_id))
        if row is None:
            return False
        await self._s.delete(row)
        await self._s.flush()
        return True

    # ---- 匹配 ----

    async def is_repo_blacklisted(self, full_name: str) -> tuple[bool, str | None]:
        """返回 (blocked, matched_pattern_or_None)。"""
        rows = await self.list_enabled(entity_type="repo")
        for r in rows:
            if _match(r.pattern, full_name):
                return True, r.pattern
        return False, None

    async def is_issue_blacklisted(
        self, full_name: str, number: int,
    ) -> tuple[bool, str | None]:
        """同时检 issue 黑名单 + repo 黑名单（repo 黑等价 issue 也黑）。"""
        # repo 层
        ok, p = await self.is_repo_blacklisted(full_name)
        if ok:
            return True, p
        # issue 层
        rows = await self.list_enabled(entity_type="issue")
        key = f"{full_name}#{number}"
        for r in rows:
            if _match(r.pattern, key):
                return True, r.pattern
        return False, None


def _match(pattern: str, value: str) -> bool:
    """fnmatch 风格匹配。* 仅在 pattern 里有效；value 直接对比。"""
    return fnmatch.fnmatchcase(value, pattern)
