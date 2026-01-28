# -*- coding: utf-8 -*-
# chats/services/cde_injection.py
#
# CDE v1 - System block injection helpers.
# NOTE: Keep code comments 7-bit ASCII only.

from __future__ import annotations

from typing import Dict, List

from chats.models import ChatWorkspace
from chats.services.cde import format_chat_goal_system_block


def locked_chat_fields_from_chat(chat: ChatWorkspace) -> Dict[str, str]:
    return {
        "chat.goal": (chat.goal_text or "").strip(),
        "chat.success": (chat.success_text or "").strip(),
        "chat.constraints": (chat.constraints_text or "").strip(),
        "chat.non_goals": (chat.non_goals_text or "").strip(),
    }


def _has_any_cde_fields(fields: Dict[str, str]) -> bool:
    return any((v or "").strip() for v in fields.values())


def _format_soft_chat_notes(fields: Dict[str, str]) -> str:
    """
    Soft notes: do not present as locked, keep language non-binding.
    """
    goal = (fields.get("chat.goal") or "").strip()
    success = (fields.get("chat.success") or "").strip()
    constraints = (fields.get("chat.constraints") or "").strip()
    non_goals = (fields.get("chat.non_goals") or "").strip()

    lines: List[str] = []
    lines.append("Soft Chat Notes (not locked):")
    if goal:
        lines.append("- Intended goal: " + goal)
    if success:
        lines.append("- Intended success: " + success)
    if constraints:
        lines.append("- Notes/constraints: " + constraints)
    if non_goals:
        lines.append("- Out of scope notes: " + non_goals)
    return "\n".join(lines) + "\n"


def build_cde_system_blocks(chat: ChatWorkspace) -> List[str]:
    """
    Return 0-1 system blocks to append to system_blocks for generate_panes.
    """
    fields = locked_chat_fields_from_chat(chat)

    # Strong steering only when controlled + locked.
    if bool(getattr(chat, "is_managed", False)) and bool(getattr(chat, "cde_is_locked", False)):
        return [format_chat_goal_system_block(locked_chat_fields=fields)]

    # Weak steering when anything is captured but not locked.
    if _has_any_cde_fields(fields):
        return [_format_soft_chat_notes(fields)]

    return []
