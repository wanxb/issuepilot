"""SQLAlchemy native enum column helper。

SQLAlchemy 2 默认用 Python enum `.name` 序列化（如 ProfileQuality.LOW → "LOW"）；
当 PG enum type 取值是 `.value`（"low"）时会 mismatch。本 helper 用
values_callable 强制按 `.value` 序列化，保证 ORM 与 PG type 定义一致。

里程碑 1.5a 新引入的 11 个 enum 在 ORM 侧都需要用 pg_enum() 而非裸 Enum()。
1.2-1.4 旧 enum 的 PG 取值恰好就是 `.name`（UPPERCASE），所以无需迁移。
"""
from __future__ import annotations

import enum as _enum
from typing import Any

from sqlalchemy import Enum as SAEnum


def pg_enum(enum_cls: type[_enum.Enum], name: str) -> SAEnum:
    """构造按 `.value` 序列化的 PG native enum 列。"""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        values_callable=lambda x: [e.value for e in x],
    )


__all__ = ["pg_enum"]
