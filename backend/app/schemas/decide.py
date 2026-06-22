"""POST /api/v1/issues/{id}/decide + /pr-closed-decide schemas。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

DecideAction = Literal["ignore", "start_dev"]
PRClosedAction = Literal["restart_dev", "archive"]


class DecideRequest(BaseModel):
    action: DecideAction


class PRClosedDecideRequest(BaseModel):
    """2.3：maintainer 关掉我们的 PR 后人工决定下一步。"""
    action: PRClosedAction
