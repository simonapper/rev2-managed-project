from __future__ import annotations

from django.db import IntegrityError, transaction

from pathlib import Path

from chats.models import ChatMessage, ChatWorkspace
from chats.services.cde_injection import build_cde_system_blocks
from chats.services.chat_bootstrap import bootstrap_chat
from chats.services.llm import generate_panes
from projects.models import Project, ProjectReviewChat
from projects.services.context_resolution import resolve_effective_context
from projects.services.llm_instructions import build_system_messages


_AGENTS_REVIEW_EXCERPT: str | None = None

def _load_review_excerpt() -> str:
    global _AGENTS_REVIEW_EXCERPT
    if _AGENTS_REVIEW_EXCERPT is not None:
        return _AGENTS_REVIEW_EXCERPT
    try:
        root = Path(__file__).resolve().parents[1]
        text = (root / "AGENTS.md").read_text(encoding="utf-8", errors="ignore")
    except Exception:
        _AGENTS_REVIEW_EXCERPT = ""
        return _AGENTS_REVIEW_EXCERPT
    start_marker = "## Review Conference Notes (Artefact Glossary + Structure)"
    end_marker = "## End Review Conference Notes"
    start = text.find(start_marker)
    if start == -1:
        _AGENTS_REVIEW_EXCERPT = ""
        return _AGENTS_REVIEW_EXCERPT
    start = text.find("\n", start) + 1
    end = text.find(end_marker, start)
    if end == -1:
        end = len(text)
    block = text[start:end].strip()
    _AGENTS_REVIEW_EXCERPT = block
    return _AGENTS_REVIEW_EXCERPT

def get_or_create_review_chat(
    *,
    project: Project,
    user,
    marker: str,
    seed_text: str,
    session_overrides: dict | None = None,
) -> ChatWorkspace:
    marker_u = (marker or "").strip().upper()
    if not marker_u:
        raise ValueError("Marker is required.")

    existing = (
        ProjectReviewChat.objects.select_related("chat")
        .filter(project=project, user=user, marker=marker_u)
        .first()
    )
    if existing:
        return existing.chat

    title = f"Review {marker_u}: {project.name}"
    seed_parts = []
    excerpt = _load_review_excerpt()
    if excerpt:
        seed_parts.append(excerpt)
    if marker_u == "INTENT":
        seed_parts.append("INTENT produces/updates the project's CKO (Canonical Knowledge Object) anchor.")
    if seed_text:
        seed_parts.append(seed_text)
    seed = "\n\n".join([s for s in seed_parts if (s or "").strip()]).strip()

    with transaction.atomic():
        existing = (
            ProjectReviewChat.objects.select_related("chat")
            .filter(project=project, user=user, marker=marker_u)
            .first()
        )
        if existing:
            return existing.chat

        chat, _ = bootstrap_chat(
            project=project,
            user=user,
            title=title,
            generate_panes_func=generate_panes,
            session_overrides={},
            cde_mode="CONTROLLED",
        )

        resolved = resolve_effective_context(
            project_id=project.id,
            user_id=user.id,
            session_overrides=session_overrides or {},
            chat_overrides={},
        )
        system_blocks = build_system_messages(resolved)
        system_blocks.extend(build_cde_system_blocks(chat))
        sys_text = "\n\n".join([b for b in system_blocks if (b or "").strip()]).strip()
        if sys_text:
            ChatMessage.objects.create(
                chat=chat,
                role=ChatMessage.Role.SYSTEM,
                raw_text=sys_text,
                answer_text=sys_text,
                segment_meta={"parser_version": "system_v1", "confidence": "N/A"},
            )

        if seed:
            ChatMessage.objects.create(
                chat=chat,
                role=ChatMessage.Role.USER,
                raw_text=seed,
                answer_text=seed,
                segment_meta={"confidence": "N/A", "parser_version": "user_v1"},
            )

        try:
            ProjectReviewChat.objects.create(
                project=project,
                user=user,
                marker=marker_u,
                chat=chat,
            )
        except IntegrityError:
            existing = ProjectReviewChat.objects.select_related("chat").get(
                project=project, user=user, marker=marker_u
            )
            chat.delete()
            return existing.chat

        return chat
