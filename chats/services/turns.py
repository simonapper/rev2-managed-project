# chats/services/turns.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from django.utils import timezone

from chats.models import ChatMessage
from uploads.models import ChatAttachment


def build_chat_turn_context(request, chat):
    """
    Turn definition (canonical):
    - One turn = one USER message + its following ASSISTANT reply
    - SYSTEM messages are not turns, but we can show them in the list
      as "events" (no turn number) for observability.
    - Handshake (ASSISTANT with no preceding USER) is not a turn
    """
    attachments = list(ChatAttachment.objects.filter(chat=chat))
    show_system = request.GET.get("system") in ("1", "true", "yes")

    msg_list = list(
        ChatMessage.objects.filter(chat=chat).order_by("sequence", "id")
    )

    def _norm_role(v: str) -> str:
        return (v or "").upper().strip()

    def _preview(text: str, n: int = 60) -> str:
        # 7-bit ASCII safe, force single-line preview
        t = (text or "").replace("\r", " ").replace("\n", " ").strip()
        while "  " in t:
            t = t.replace("  ", " ")
        return (t[:n] + "...") if len(t) > n else t


    turns = []
    system_items = []
    pending_user = None

    for m in msg_list:
        role = _norm_role(m.role)

        if role == "SYSTEM":
            if show_system:
                system_items.append({
                    "turn_id": f"sys-{m.id}",
                    "kind": "system",
                    "number": "",
                    "input": None,
                    "assistant": None,
                    "answer": "",
                    "reasoning": "",
                    "output": (m.raw_text or "").strip(),
                    "created_at": m.created_at,
                    "title": _preview(m.raw_text or "(system)"),
                })
            continue


        if role == "USER":
            pending_user = m
            continue

        if role == "ASSISTANT":
            # Ignore handshake / orphan assistant messages (no preceding USER)
            if pending_user is None:
                continue

            answer = (m.answer_text or "").strip()
            reasoning = (m.reasoning_text or "").strip()
            output = (m.output_text or "").strip()

            # Legacy fallback: panes not stored yet
            if not (answer or reasoning or output):
                answer = (m.raw_text or "").strip()

            turns.append({
                "turn_id": f"seq-{m.sequence}",
                "kind": "turn",
                "input": pending_user,
                "assistant": m,
                "answer": answer,
                "reasoning": reasoning,
                "output": output,
                "created_at": pending_user.created_at if pending_user else m.created_at,
                "title": _preview((pending_user.raw_text if pending_user else "") or "(no input)"),
            })

            pending_user = None
            continue

    if pending_user is not None:
        turns.append({
            "turn_id": f"pending-{pending_user.id}",
            "kind": "turn",
            "input": pending_user,
            "assistant": None,
            "answer": "",
            "reasoning": "",
            "output": "",
            "created_at": pending_user.created_at,
            "title": _preview(pending_user.raw_text or "(no input)"),
        })
        pending_user = None

    # Number turns (1..N) in chronological construction order
    n = 0
    for t in turns:
        n += 1
        t["number"] = n

    # Merge: SYSTEM events + turns, then sort by created_at/id for "right order"
    items = system_items + turns
    items = sorted(
        items,
        key=lambda x: (x.get("created_at") or timezone.now(), x.get("turn_id", "")),
    )

    # Re-number turns again after merge/sort; SYSTEM stays blank
    n = 0
    for it in items:
        if it.get("kind") == "turn":
            n += 1
            it["number"] = n
        else:
            it["number"] = ""

    # Active selection (turn or system event)
    selected_turn_id = request.GET.get("turn")
    active_turn = None
    if selected_turn_id:
        active_turn = next((t for t in items if t["turn_id"] == selected_turn_id), None)
    if active_turn is None and items:
        active_turn = items[-1]
    is_system_turn = bool(active_turn) and str(active_turn.get("turn_id", "")).startswith("sys-")
    turn_sort, turn_dir = normalise_turn_sort(request)

    # If user did not explicitly choose a sort, default to chronological
    if "turn_sort" not in request.GET:
        turn_sort = "updated"
        turn_dir = "asc"

    key_map = {
        "number": lambda x: (x.get("created_at") or timezone.now()),
        "title": lambda x: (x.get("title") or ""),
        "updated": lambda x: (x.get("created_at") or timezone.now()),
    }
    key_fn = key_map.get(turn_sort, key_map["updated"])
    items = sorted(items, key=key_fn, reverse=(turn_dir == "desc"))

    # If no explicit selection, refresh active to latest TURN after sorting
    if not selected_turn_id and items:
        last_turn = None
        for it in reversed(items):
            if it.get("kind") == "turn":
                last_turn = it
                break
        active_turn = last_turn or items[-1]
        is_system_turn = bool(active_turn) and str(active_turn.get("turn_id", "")).startswith("sys-")


    # Re-number turns after user sort; SYSTEM stays blank
    n = 0
    # for it in items:
    #     if it.get("kind") == "turn":
    #         n += 1
    #         it["number"] = n
    #     else:
    #         it["number"] = ""

    return {
        "attachments": attachments,
        "turn_items": items,
        "turn_items_rev": list(reversed(items)),
        "active_turn": active_turn,
        "turn_sort": turn_sort,
        "turn_dir": turn_dir,
        "is_system_turn": is_system_turn,
        "show_system": show_system,
    }


def normalise_turn_sort(request):
    sort = request.GET.get("turn_sort", "number")
    direction = request.GET.get("turn_dir", "asc")

    if sort not in {"number", "title", "updated"}:
        sort = "number"
    if direction not in {"asc", "desc"}:
        direction = "asc"

    return sort, direction
