# chats/services/chat_bootstrap.py
# -*- coding: utf-8 -*-

from chats.models import ChatWorkspace, ChatMessage, ChatSnapshot
from projects.services.context_resolution import resolve_effective_context
from projects.services.llm_instructions import build_system_messages
from chats.services.llm import generate_handshake


def bootstrap_chat(*, project, user, title=None) -> ChatWorkspace:
    """
    Create a new chat, resolve effective context, persist SYSTEM blocks,
    call the LLM handshake, persist the first ASSISTANT message, then snapshot provenance.

    Idempotency rule (Option B):
    - If title is not provided (auto-provision), reuse the earliest chat ONLY if it already
      has an ASSISTANT message. If it is SYSTEM-only, create a new chat (or run bootstrap again).
    """

    # Idempotency: only reuse existing auto-provision chat if it already has assistant output
    if not title:
        existing = (
            ChatWorkspace.objects
            .filter(project=project, created_by=user)
            .order_by("created_at")
            .first()
        )
        if existing:
            has_assistant = ChatMessage.objects.filter(
                chat=existing,
                role=ChatMessage.Role.ASSISTANT,
            ).exists()
            if has_assistant:
                return existing

   # 1) Create chat
    chat = ChatWorkspace.objects.create(
        project=project,
        created_by=user,
        title=title or "New chat",
    )

    # 2) Resolve effective context
    resolved = resolve_effective_context(
        project_id=project.id,
        user_id=user.id,
        session_overrides=getattr(chat, "chat_overrides", None),
    )

    # 3) Build SYSTEM messages
    system_blocks = build_system_messages(resolved)

    # 4) Persist SYSTEM messages
    for block in system_blocks:
        ChatMessage.objects.create(
            chat=chat,
            role=ChatMessage.Role.SYSTEM,
            raw_text=block,
        )

    # 4b) LLM handshake + persist assistant
    try:
        assistant_text = generate_handshake(
            system_blocks=system_blocks,
            first_name=getattr(user, "first_name", None),
        )
    except Exception as e:
        assistant_text = "LLM handshake failed: " + str(e)

    ChatMessage.objects.create(
        chat=chat,
        role=ChatMessage.Role.ASSISTANT,
        raw_text=assistant_text,
    )

    # 5) Write immutable snapshot
    prov = resolved.get("provenance", {}) or {}

    ChatSnapshot.objects.create(
        chat=chat,
        project=project,
        l1_ref=str(prov.get("l1") or "L1-NONE"),
        l2_ref=str(prov.get("l2") or "L2-SYSTEM"),
        l3_ref=str(prov.get("l3") or "L3-NONE"),
        user_prefs_ref=str(prov.get("user_prefs") or "PREFS-NONE"),
        overrides_hash=str(prov.get("overrides_hash") or ""),
    )

    return chat
