# -*- coding: utf-8 -*-
# chats/services/cde_spec.py
#
# CDE v1 - Required fields and ordering for chat definition.
# NOTE: Keep code comments 7-bit ASCII only.

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .cde_rubrics import CDE_FIELD_RUBRICS


@dataclass(frozen=True)
class CDEFieldSpec:
    key: str
    label: str
    required: bool = True
    rubric: Optional[str] = None


CDE_REQUIRED_FIELDS: List[CDEFieldSpec] = [
    CDEFieldSpec(
        key="chat.goal",
        label="Chat goal",
        required=True,
        rubric=CDE_FIELD_RUBRICS.get("chat.goal"),
    ),
    CDEFieldSpec(
        key="chat.success",
        label="Success criteria",
        required=True,
        rubric=CDE_FIELD_RUBRICS.get("chat.success"),
    ),
    CDEFieldSpec(
        key="chat.constraints",
        label="Constraints",
        required=True,
        rubric=CDE_FIELD_RUBRICS.get("chat.constraints"),
    ),
    CDEFieldSpec(
        key="chat.non_goals",
        label="Out of scope (non-goals)",
        required=True,
        rubric=CDE_FIELD_RUBRICS.get("chat.non_goals"),
    ),
]
