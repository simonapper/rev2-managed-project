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


def _as_list(v) -> List[str]:
    if isinstance(v, list):
        out: List[str] = []
        for x in v:
            t = str(x).strip()
            if t:
                out.append(t)
        return out
    if isinstance(v, str):
        t = v.strip()
        return [t] if t else []
    return []


def _bullets(items: List[str], max_n: int) -> List[str]:
    out: List[str] = []
    for t in (items or [])[: max_n or 0]:
        tt = (t or "").strip()
        if tt:
            out.append("  - " + tt)
    return out


def _extras_from_cde_json(chat: ChatWorkspace) -> Dict[str, List[str]]:
    """
    Pull optional AI-native extras from chat.cde_json.

    Expected shape (loose):
    {
      "hypotheses": {
        "assumptions": ["..."],
        "open_questions": ["..."]
      }
    }

    We keep this tolerant: missing keys -> empty lists.
    """
    cj = getattr(chat, "cde_json", None) or {}
    if not isinstance(cj, dict):
        return {"assumptions": [], "open_questions": []}

    h = cj.get("hypotheses")
    if not isinstance(h, dict):
        h = {}

    assumptions = _as_list(h.get("assumptions"))
    open_questions = _as_list(h.get("open_questions"))

    return {"assumptions": assumptions, "open_questions": open_questions}


def _format_soft_chat_notes(fields: Dict[str, str], extras: Dict[str, List[str]]) -> str:
    """
    Soft notes: do not present as locked, keep language non-binding.
    """
    goal = (fields.get("chat.goal") or "").strip()
    success = (fields.get("chat.success") or "").strip()
    constraints = (fields.get("chat.constraints") or "").strip()
    non_goals = (fields.get("chat.non_goals") or "").strip()

    assumptions = extras.get("assumptions") or []
    open_questions = extras.get("open_questions") or []

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

    if assumptions:
        lines.append("- Assumptions:")
        lines.extend(_bullets(assumptions, 5))

    if open_questions:
        lines.append("- Open questions:")
        lines.extend(_bullets(open_questions, 3))

    return "\n".join(lines) + "\n"


def _append_extras_to_locked_block(block: str, extras: Dict[str, List[str]]) -> str:
    assumptions = extras.get("assumptions") or []
    open_questions = extras.get("open_questions") or []

    if not assumptions and not open_questions:
        return block

    lines: List[str] = [block.rstrip("\n")]

    if assumptions:
        lines.append("Assumptions:")
        lines.extend(_bullets(assumptions, 5))

    if open_questions:
        lines.append("Open questions:")
        lines.extend(_bullets(open_questions, 3))

    return "\n".join(lines).rstrip("\n") + "\n"


def build_cde_system_blocks(chat: ChatWorkspace) -> List[str]:
    """
    Return 0-1 system blocks to append to system_blocks for generate_panes.
    """
    fields = locked_chat_fields_from_chat(chat)
    extras = _extras_from_cde_json(chat)

    has_extras = bool(extras.get("assumptions") or extras.get("open_questions"))

    # Strong steering only when controlled + locked.
    if bool(getattr(chat, "is_managed", False)) and bool(getattr(chat, "cde_is_locked", False)):
        base = format_chat_goal_system_block(locked_chat_fields=fields)
        return [_append_extras_to_locked_block(base, extras)]

    # Weak steering when anything is captured but not locked.
    if _has_any_cde_fields(fields) or has_extras:
        return [_format_soft_chat_notes(fields, extras)]

    return []
