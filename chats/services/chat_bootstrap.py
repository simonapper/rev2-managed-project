# chats/services/chat_bootstrap.py
# -*- coding: utf-8 -*-

from chats.models import ChatWorkspace, ChatMessage, ChatSnapshot
from projects.services.context_resolution import resolve_effective_context
from projects.services.llm_instructions import build_system_messages, build_boot_dump_level2_text
from chats.services.llm import generate_handshake

from chats.services.cde_loop import run_cde
from chats.services.cde_injection import build_cde_system_blocks


def bootstrap_chat(
    *,
    project,
    user,
    generate_panes_func,
    title=None,
    cde_mode="SKIP",          # "SKIP" | "LOOSE" | "CONTROLLED"
    cde_inputs=None,          # dict keyed by "chat.goal" etc
    session_overrides=None,   # dict from request.session (or {})
) -> ChatWorkspace:
    """
    Create a new chat, resolve effective context, persist SYSTEM blocks,
    call the LLM handshake, persist the first ASSISTANT message, then snapshot provenance.

    Idempotency rule (Option B):
    - If title is not provided (auto-provision), reuse the earliest chat ONLY if it already
      has an ASSISTANT message. If it is SYSTEM-only, create a new chat (or run bootstrap again).

    CDE:
    - SKIP: unmanaged, no direction.
    - LOOSE: best-effort capture (not locked).
    - CONTROLLED: must PASS/lock to set cde_is_locked True.
    """

    cde_mode = (cde_mode or "SKIP").strip().upper()
    cde_inputs = cde_inputs or {}
    session_overrides = session_overrides or {}

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

    # 1b) Optional CDE
    if cde_mode in ("LOOSE", "CONTROLLED"):
        run_cde(
            chat=chat,
            generate_panes_func=generate_panes_func,
            user_inputs=cde_inputs,
            mode=cde_mode,
        )
    else:
        # Explicit SKIP (or unknown): keep unmanaged, unlocked.
        chat.is_managed = False
        chat.cde_is_locked = False
        chat.save(update_fields=["is_managed", "cde_is_locked", "updated_at"])

    # 2) Resolve effective context (correct wiring)
    resolved = resolve_effective_context(
        project_id=project.id,
        user_id=user.id,
        session_overrides=session_overrides,
        chat_overrides=(chat.chat_overrides or {}),
    )

    # 3) Build LLM-facing boot SYSTEM messages
    system_blocks = build_system_messages(resolved)

    # 3a) Append CDE direction (managed locked -> strong; else soft; else nothing)
    system_blocks.extend(build_cde_system_blocks(chat))

    # 3b) Boot-only Level 2 dump (for UI/audit only; do NOT persist as ChatMessage)
    _l2_dump_text = build_boot_dump_level2_text(resolved)

    # 4) Persist SYSTEM messages (small, LLM-facing only)
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
