# -*- coding: utf-8 -*-
# chats/services/cde_loop.py
#
# CDE v1 - Run the chat definition flow for either loose or controlled chats.
# NOTE: Keep code comments 7-bit ASCII only.

from __future__ import annotations

from typing import Any, Dict, List, Optional

from chats.models import ChatWorkspace
from chats.services.cde import validate_field
from chats.services.cde_spec import CDE_REQUIRED_FIELDS


def _set_chat_field(chat: ChatWorkspace, field_key: str, value: str) -> None:
    v = (value or "").strip()
    if field_key == "chat.goal":
        chat.goal_text = v
    elif field_key == "chat.success":
        chat.success_text = v
    elif field_key == "chat.constraints":
        chat.constraints_text = v
    elif field_key == "chat.non_goals":
        chat.non_goals_text = v


def run_cde(
    *,
    chat: ChatWorkspace,
    generate_panes_func,
    user_inputs: Dict[str, str],
    mode: str,  # "LOOSE" or "CONTROLLED"
    save_loose_partials: bool = True,
) -> Dict[str, Any]:
    """
    LOOSE:
    - Capture whatever is provided; no validation, no locking.
    CONTROLLED:
    - Validate in order, require PASS, lock values, set is_managed=True, cde_is_locked=True.

    Returns:
    { "ok": bool, "mode": str, "locked": bool, "results": [...], "first_blocker": obj|None }
    """
    mode = (mode or "").strip().upper()
    if mode not in ("LOOSE", "CONTROLLED"):
        mode = "LOOSE"

    results: List[Dict[str, Any]] = []
    first_blocker: Optional[Dict[str, Any]] = None

    if mode == "LOOSE":
        chat.is_managed = False
        chat.cde_is_locked = False

        if save_loose_partials:
            for spec in CDE_REQUIRED_FIELDS:
                proposed = (user_inputs.get(spec.key) or "").strip()
                if proposed:
                    _set_chat_field(chat, spec.key, proposed)

            chat.save(
                update_fields=[
                    "is_managed",
                    "cde_is_locked",
                    "goal_text",
                    "success_text",
                    "constraints_text",
                    "non_goals_text",
                    "updated_at",
                ]
            )
        else:
            chat.save(update_fields=["is_managed", "cde_is_locked", "updated_at"])

        return {
            "ok": True,
            "mode": "LOOSE",
            "locked": False,
            "results": results,
            "first_blocker": None,
        }

    # CONTROLLED
    chat.is_managed = True
    chat.cde_is_locked = False

    locked_fields: Dict[str, str] = {}

    for spec in CDE_REQUIRED_FIELDS:
        field_key = spec.key
        proposed = (user_inputs.get(field_key) or "").strip()

        vobj = validate_field(
            generate_panes_func=generate_panes_func,
            field_key=field_key,
            value_text=proposed,
            locked_fields=locked_fields,
            rubric=spec.rubric,
        )
        results.append(vobj)

        if vobj.get("verdict") != "PASS":
            first_blocker = vobj
            break

        locked_value = (vobj.get("suggested_revision") or proposed).strip()
        locked_fields[field_key] = locked_value
        _set_chat_field(chat, field_key, locked_value)

    ok = first_blocker is None and len(results) == len(CDE_REQUIRED_FIELDS)

    if ok:
        chat.cde_is_locked = True
        chat.save(
            update_fields=[
                "is_managed",
                "cde_is_locked",
                "goal_text",
                "success_text",
                "constraints_text",
                "non_goals_text",
                "updated_at",
            ]
        )
    else:
        # Controlled but not locked yet.
        # If you want to persist "draft" values, add the text fields to update_fields here.
        chat.save(update_fields=["is_managed", "cde_is_locked", "updated_at"])

    return {
        "ok": ok,
        "mode": "CONTROLLED",
        "locked": ok,
        "results": results,
        "first_blocker": first_blocker,
    }
