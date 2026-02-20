# -*- coding: utf-8 -*-
# projects/services_topic_chat.py

from __future__ import annotations

from typing import Dict

from django.db import IntegrityError, transaction

from chats.models import ChatMessage, ChatWorkspace
from chats.services.chat_bootstrap import bootstrap_chat
from chats.services.llm import generate_panes
from projects.models import Project, ProjectTopicChat


def _build_cde_inputs(*, label: str) -> Dict[str, str]:
    goal = f"Improve the {label} section for this project."
    success = "A replacement draft ready to paste into the section."
    constraints = "Stay within this section only. Follow the output format requested. Use British English."
    non_goals = "Do not discuss other sections or general project planning."
    return {
        "chat.goal": goal,
        "chat.success": success,
        "chat.constraints": constraints,
        "chat.non_goals": non_goals,
    }


def get_or_create_topic_chat(
    *,
    project: Project,
    user,
    scope: str,
    topic_key: str,
    title: str,
    seed_user_text: str,
    mode: str,
) -> ChatWorkspace:
    scope_u = (scope or "").strip().upper()
    topic_key_n = (topic_key or "").strip()
    if not scope_u or not topic_key_n:
        raise ValueError("Scope and topic_key are required.")

    existing = (
        ProjectTopicChat.objects.select_related("chat")
        .filter(project=project, user=user, scope=scope_u, topic_key=topic_key_n)
        .first()
    )
    if existing:
        desired_title = (title or "").strip()
        if desired_title and existing.chat and (existing.chat.title or "") != desired_title:
            existing.chat.title = desired_title
            existing.chat.save(update_fields=["title"])
        return existing.chat

    cde_mode = (mode or "CONTROLLED").strip().upper() or "CONTROLLED"
    label = (title or topic_key_n).strip()
    cde_inputs = _build_cde_inputs(label=label)

    with transaction.atomic():
        existing = (
            ProjectTopicChat.objects.select_related("chat")
            .filter(project=project, user=user, scope=scope_u, topic_key=topic_key_n)
            .first()
        )
        if existing:
            return existing.chat

        chat, _cde_result = bootstrap_chat(
            project=project,
            user=user,
            title=title,
            generate_panes_func=generate_panes,
            session_overrides={},
            cde_mode=cde_mode,
            cde_inputs=cde_inputs,
            skip_readiness_checks=True,
        )

        seed = (seed_user_text or "").strip()
        if seed:
            ChatMessage.objects.create(
                chat=chat,
                role=ChatMessage.Role.USER,
                raw_text=seed,
                answer_text=seed,
                segment_meta={"confidence": "N/A", "parser_version": "user_v1"},
            )

        try:
            ProjectTopicChat.objects.create(
                project=project,
                user=user,
                scope=scope_u,
                topic_key=topic_key_n,
                chat=chat,
            )
        except IntegrityError:
            existing = ProjectTopicChat.objects.select_related("chat").get(
                project=project, user=user, scope=scope_u, topic_key=topic_key_n
            )
            chat.delete()
            return existing.chat

        return chat
