from __future__ import annotations

from django.db import IntegrityError, transaction

from chats.models import ChatMessage, ChatWorkspace
from chats.services.cde_injection import build_cde_system_blocks
from chats.services.chat_bootstrap import bootstrap_chat
from chats.services.llm import generate_panes
from projects.models import Project, ProjectReviewChat, ProjectReviewStageChat
from projects.services_artefacts import build_cko_seed_text, get_conference_seed_excerpt, get_pdo_schema_text
from projects.services_execute_validator import build_execute_conference_seed, build_execute_stage_seed
from projects.services.context_resolution import resolve_effective_context
from projects.services.llm_instructions import build_system_messages

def get_or_create_review_chat(
    *,
    project: Project,
    user,
    marker: str,
    seed_text: str,
    seed_from_anchor: dict | None = None,
    pdo_target: str | None = None,
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
    seed_parts.append("This is a controlled Review Conference.")
    excerpt = get_conference_seed_excerpt(marker_u)
    if excerpt:
        seed_parts.append(excerpt)
    if marker_u == "INTENT":
        seed_parts.append("INTENT produces/updates the project's CKO (Canonical Knowledge Object) anchor.")
    if seed_from_anchor:
        seed_parts.append("Seed source (locked anchor):")
        seed_parts.append(seed_from_anchor)
    if marker_u == "ROUTE":
        seed_parts.append("Target PDO JSON (final output only when ready):")
        seed_parts.append(pdo_target or get_pdo_schema_text())
    seed_parts.append(
        "Interrogate the anchor, propose edits, and ask focused questions to reach a stable version suitable for acceptance."
    )
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


def get_or_create_review_stage_chat(
    *,
    project: Project,
    user,
    marker: str,
    stage_number: int,
    seed_text: str,
    session_overrides: dict | None = None,
) -> ChatWorkspace:
    marker_u = (marker or "").strip().upper()
    if not marker_u:
        raise ValueError("Marker is required.")

    existing = (
        ProjectReviewStageChat.objects.select_related("chat")
        .filter(project=project, user=user, marker=marker_u, stage_number=stage_number)
        .first()
    )
    if existing:
        return existing.chat

    title = f"Review {marker_u} Stage {stage_number}: {project.name}"
    if marker_u == "EXECUTE":
        seed_parts = [
            "This is a controlled Review Conference (stage).",
            seed_text,
        ]
    else:
        seed_parts = [
            "This is a controlled Review Conference (stage).",
            "Goal: refine this single stage and return JSON only for the stage when ready.",
            "Stage JSON target:",
            "{",
            "  \"stage_id\": \"S" + str(stage_number) + "\",",
            "  \"stage_number\": " + str(stage_number) + ",",
            "  \"status\": \"\",",
            "  \"title\": \"\",",
            "  \"purpose\": \"\",",
            "  \"inputs\": \"\",",
            "  \"stage_process\": \"\",",
            "  \"outputs\": \"\",",
            "  \"assumptions\": \"\",",
            "  \"duration_estimate\": \"\",",
            "  \"risks_notes\": \"\"",
            "}",
            seed_text,
        ]
    seed = "\n".join([s for s in seed_parts if (s or "").strip()]).strip()

    with transaction.atomic():
        existing = (
            ProjectReviewStageChat.objects.select_related("chat")
            .filter(project=project, user=user, marker=marker_u, stage_number=stage_number)
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
            ProjectReviewStageChat.objects.create(
                project=project,
                user=user,
                marker=marker_u,
                stage_number=stage_number,
                chat=chat,
            )
        except IntegrityError:
            existing = ProjectReviewStageChat.objects.select_related("chat").get(
                project=project, user=user, marker=marker_u, stage_number=stage_number
            )
            chat.delete()
            return existing.chat

        return chat
