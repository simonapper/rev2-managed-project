# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from django.utils import timezone

from chats.models import ChatMessage, ChatRollupEvent, ChatWorkspace
from chats.services.llm import generate_text


def _cursor_id(chat: ChatWorkspace) -> int:
    try:
        return int(chat.pinned_cursor_message_id or 0)
    except Exception:
        return 0


def _eligible_messages_qs(chat: ChatWorkspace):
    cursor = _cursor_id(chat)
    return (
        ChatMessage.objects.filter(chat=chat, id__gt=cursor)
        .filter(role__in=[ChatMessage.Role.USER, ChatMessage.Role.ASSISTANT])
        .exclude(importance=ChatMessage.Importance.IGNORE)
        .order_by("id")
    )


def count_active_window_messages(chat: ChatWorkspace) -> int:
    return _eligible_messages_qs(chat).count()


def count_active_window_turns(chat: ChatWorkspace) -> int:
    msgs = list(_eligible_messages_qs(chat))
    pending_user = None
    turns = 0
    for m in msgs:
        if m.role == ChatMessage.Role.USER:
            pending_user = m
        elif m.role == ChatMessage.Role.ASSISTANT and pending_user is not None:
            turns += 1
            pending_user = None
    return turns


def should_auto_rollup(chat: ChatWorkspace, *, user: Any = None) -> bool:
    trigger_count = 20
    try:
        profile = None
        if user is not None:
            profile = getattr(user, "profile", None)
        if profile is None and getattr(chat, "created_by", None) is not None:
            profile = getattr(chat.created_by, "profile", None)
        raw = getattr(profile, "summary_rollup_trigger_message_count", None)
        if raw is not None:
            trigger_count = int(raw)
    except Exception:
        trigger_count = 20

    if trigger_count < 2:
        trigger_count = 2
    return count_active_window_messages(chat) >= trigger_count


def build_history_messages(
    chat: ChatWorkspace,
    *,
    answer_mode: str,
    exclude_message_ids: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    exclude_message_ids = exclude_message_ids or []
    msgs = list(_eligible_messages_qs(chat).exclude(id__in=exclude_message_ids))
    if answer_mode == "quick":
        pending_user = None
        turns: List[tuple[ChatMessage, ChatMessage]] = []
        for m in msgs:
            if m.role == ChatMessage.Role.USER:
                pending_user = m
            elif m.role == ChatMessage.Role.ASSISTANT and pending_user is not None:
                turns.append((pending_user, m))
                pending_user = None
        if turns:
            msgs = [turns[-1][0], turns[-1][1]]
        else:
            msgs = []

    out: List[Dict[str, Any]] = []
    for m in msgs:
        if m.role == ChatMessage.Role.USER:
            text = (m.raw_text or m.answer_text or "").strip()
            if text:
                out.append({"role": "user", "content": [{"type": "input_text", "text": text}]})
        elif m.role == ChatMessage.Role.ASSISTANT:
            text = (m.answer_text or m.raw_text or "").strip()
            if text:
                out.append({"role": "assistant", "content": [{"type": "output_text", "text": text}]})
    return out


def build_pinned_system_block(chat: ChatWorkspace) -> str:
    summary = (chat.pinned_summary or "").strip()
    conclusion = (chat.pinned_conclusion or "").strip()
    if not summary and not conclusion:
        return ""
    lines = ["Rolling summary context. Treat as authoritative compressed history."]
    if summary:
        lines.append("")
        lines.append("Summary:")
        lines.append(summary)
    if conclusion:
        lines.append("")
        lines.append("Conclusion:")
        lines.append(conclusion)
    return "\n".join(lines).strip()


def _messages_for_rollup(chat: ChatWorkspace, *, upto_message_id: Optional[int] = None) -> List[ChatMessage]:
    qs = _eligible_messages_qs(chat)
    if upto_message_id is not None:
        qs = qs.filter(id__lte=upto_message_id)
    return list(qs)


def _extract_json_dict(raw_text: str) -> Optional[Dict[str, Any]]:
    text = (raw_text or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    if "```" in text:
        parts = text.split("```")
        for chunk in parts[1::2]:
            c = chunk.strip()
            if c.lower().startswith("json"):
                c = c[4:].strip()
            try:
                obj = json.loads(c)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                continue
    return None


def rollup_segment(
    chat: ChatWorkspace,
    *,
    upto_message_id: Optional[int] = None,
    user: Any = None,
    trigger: str = ChatRollupEvent.Trigger.AUTO,
) -> Dict[str, Any]:
    segment = _messages_for_rollup(chat, upto_message_id=upto_message_id)
    if not segment:
        return {"ok": True, "rolled": False}

    transcript_lines: List[str] = []
    for m in segment:
        role = "USER" if m.role == ChatMessage.Role.USER else "ASSISTANT"
        text = (m.raw_text or m.answer_text or "").strip()
        if text:
            transcript_lines.append(role + ":\n" + text)
    transcript_text = "\n\n".join(transcript_lines).strip()

    existing_summary = (chat.pinned_summary or "").strip()
    existing_conclusion = (chat.pinned_conclusion or "").strip()
    existing_cursor = chat.pinned_cursor_message_id

    system_blocks = [
        "Update a rolling summary for future context injection.",
        "Return JSON:",
        "{",
        '  "summary": "bullet points (5-10)",',
        '  "conclusion": "short paragraph"',
        "}",
        "Return JSON only.",
    ]
    user_text = (
        "Existing summary:\n"
        + (existing_summary or "(none)")
        + "\n\nExisting conclusion:\n"
        + (existing_conclusion or "(none)")
        + "\n\nNew transcript segment:\n"
        + (transcript_text or "(none)")
    )

    try:
        raw = generate_text(
            system_blocks=system_blocks,
            messages=[{"role": "user", "content": user_text}],
            user=(user or getattr(chat, "created_by", None)),
        )
        payload = _extract_json_dict(raw)
        if payload is not None:
            chat.pinned_summary = str(payload.get("summary") or "").strip()
            chat.pinned_conclusion = str(payload.get("conclusion") or "").strip()
        else:
            chat.pinned_summary = (raw or "").strip()
            chat.pinned_conclusion = ""
    except Exception as exc:
        chat.pinned_summary = "Roll-up failed: " + str(exc)
        chat.pinned_conclusion = ""

    chat.pinned_cursor_message_id = segment[-1].id
    chat.pinned_updated_at = timezone.now()
    chat.save(
        update_fields=[
            "pinned_summary",
            "pinned_conclusion",
            "pinned_cursor_message_id",
            "pinned_updated_at",
            "updated_at",
        ]
    )
    trigger_message = segment[-1] if trigger == ChatRollupEvent.Trigger.PIN else None
    ChatRollupEvent.objects.create(
        chat=chat,
        trigger=trigger,
        trigger_message=trigger_message,
        prev_summary=existing_summary,
        prev_conclusion=existing_conclusion,
        prev_cursor_message_id=existing_cursor,
        new_summary=(chat.pinned_summary or ""),
        new_conclusion=(chat.pinned_conclusion or ""),
        new_cursor_message_id=chat.pinned_cursor_message_id,
        created_by=(user if getattr(user, "is_authenticated", False) else None),
    )
    return {"ok": True, "rolled": True, "cursor": chat.pinned_cursor_message_id}


def undo_last_rollup(chat: ChatWorkspace, *, user: Any = None) -> Dict[str, Any]:
    ev = (
        ChatRollupEvent.objects.filter(chat=chat, reverted_at__isnull=True)
        .order_by("-created_at", "-id")
        .first()
    )
    if ev is None:
        return {"ok": True, "undone": False}

    chat.pinned_summary = ev.prev_summary or ""
    chat.pinned_conclusion = ev.prev_conclusion or ""
    chat.pinned_cursor_message_id = ev.prev_cursor_message_id
    chat.pinned_updated_at = timezone.now()
    chat.save(
        update_fields=[
            "pinned_summary",
            "pinned_conclusion",
            "pinned_cursor_message_id",
            "pinned_updated_at",
            "updated_at",
        ]
    )
    ev.reverted_at = timezone.now()
    ev.reverted_by = (user if getattr(user, "is_authenticated", False) else None)
    ev.save(update_fields=["reverted_at", "reverted_by"])
    return {"ok": True, "undone": True, "cursor": chat.pinned_cursor_message_id}
