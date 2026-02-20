# chats/services/turns.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from django.utils import timezone

from chats.models import ChatMessage
from chats.services.llm import _extract_json_dict_from_text
from uploads.models import ChatAttachment, GeneratedImage


def build_chat_turn_context(request, chat):
    """
    Turn definition (canonical):
    - One turn = one USER message + its following ASSISTANT reply
    - SYSTEM messages are not turns, but we can show them in the list
      as "events" (no turn number) for observability.
    - Handshake (ASSISTANT with no preceding USER) is not a turn
    """
    attachments = list(ChatAttachment.objects.filter(chat=chat))
    generated_images = list(GeneratedImage.objects.filter(chat=chat).order_by("created_at", "id"))
    images_by_message_id = {}
    for gi in generated_images:
        if gi.message_id:
            images_by_message_id.setdefault(gi.message_id, []).append(gi)
    show_system = request.GET.get("system") in ("1", "true", "yes")
    cursor_id = int(getattr(chat, "pinned_cursor_message_id", 0) or 0)

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

    def _coerce_text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            lines = []
            for item in value:
                lines.append("- " + str(item))
            return "\n".join(lines).strip()
        if isinstance(value, dict):
            return str(value).strip()
        return str(value).strip()

    def _recover_panes_from_blob(blob: str):
        payload = _extract_json_dict_from_text(blob or "")
        if not isinstance(payload, dict):
            return None
        rec_answer = _coerce_text(payload.get("answer"))
        rec_reasoning = _coerce_text(payload.get("reasoning"))
        rec_output = _coerce_text(payload.get("output"))
        return rec_answer, rec_reasoning, rec_output


    turns = []
    system_items = []
    pending_user = None

    for m in msg_list:
        role = _norm_role(m.role)

        if role == "SYSTEM":
            if show_system:
                is_rolled_up = bool(cursor_id and m.id <= cursor_id)
                system_items.append({
                    "turn_id": f"sys-{m.id}",
                    "kind": "system",
                    "number": "",
                    "input": None,
                    "assistant": None,
                    "input_message_id": None,
                    "assistant_message_id": None,
                    "answer": "",
                    "reasoning": "",
                    "output": (m.raw_text or "").strip(),
                    "created_at": m.created_at,
                    "title": _preview(m.raw_text or "(system)"),
                    "importance": getattr(m, "importance", "NORMAL"),
                    "is_pinned": getattr(m, "importance", "") == "PINNED",
                    "is_ignored": getattr(m, "importance", "") == "IGNORE",
                    "is_rolled_up": is_rolled_up,
                    "generated_images": images_by_message_id.get(m.id, []),
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
            elif answer and not (reasoning or output):
                recovered = _recover_panes_from_blob(answer)
                if recovered:
                    rec_answer, rec_reasoning, rec_output = recovered
                    if rec_answer:
                        answer = rec_answer
                    if rec_reasoning:
                        reasoning = rec_reasoning
                    if rec_output:
                        output = rec_output

            turns.append({
                "turn_id": f"msg-{m.id}",
                "legacy_turn_id": f"seq-{m.sequence}",
                "kind": "turn",
                "input": pending_user,
                "assistant": m,
                "input_message_id": pending_user.id if pending_user else None,
                "assistant_message_id": m.id,
                "answer": answer,
                "reasoning": reasoning,
                "output": output,
                "created_at": pending_user.created_at if pending_user else m.created_at,
                "title": _preview((pending_user.raw_text if pending_user else "") or "(no input)"),
                "importance": getattr(m, "importance", "NORMAL"),
                "is_pinned": (
                    getattr(m, "importance", "") == "PINNED"
                    or (pending_user is not None and getattr(pending_user, "importance", "") == "PINNED")
                ),
                "is_ignored": (
                    getattr(m, "importance", "") == "IGNORE"
                    or (pending_user is not None and getattr(pending_user, "importance", "") == "IGNORE")
                ),
                "is_rolled_up": bool(cursor_id and m.id <= cursor_id),
                "generated_images": images_by_message_id.get(m.id, []),
            })

            pending_user = None
            continue

    if pending_user is not None:
        turns.append({
            "turn_id": f"pending-{pending_user.id}",
            "legacy_turn_id": "",
            "kind": "turn",
            "input": pending_user,
            "assistant": None,
            "input_message_id": pending_user.id,
            "assistant_message_id": None,
            "answer": "",
            "reasoning": "",
            "output": "",
            "created_at": pending_user.created_at,
            "title": _preview(pending_user.raw_text or "(no input)"),
            "importance": getattr(pending_user, "importance", "NORMAL"),
            "is_pinned": getattr(pending_user, "importance", "") == "PINNED",
            "is_ignored": getattr(pending_user, "importance", "") == "IGNORE",
            "is_rolled_up": bool(cursor_id and pending_user.id <= cursor_id),
            "generated_images": [],
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
        active_turn = next(
            (
                t
                for t in items
                if t.get("turn_id") == selected_turn_id
                or t.get("legacy_turn_id") == selected_turn_id
            ),
            None,
        )
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
