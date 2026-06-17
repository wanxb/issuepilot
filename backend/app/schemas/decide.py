"""POST /api/v1/issues/{id}/decide schema。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

DecideAction = Literal["ignore", "start_dev"]


class DecideRequest(BaseModel):
    action: DecideAction
