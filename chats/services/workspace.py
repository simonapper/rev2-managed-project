# -*- coding: utf-8 -*-
# chats/services/workspace.py

from __future__ import annotations

from django.db import transaction

from projects.context import resolve_effective_context
from chats.models import ChatWorkspace


@transaction.atomic
def create_chat(
    *,
    project,
    user,
    title: str,
    folder=None,
    session_overrides: dict | None = None,
) -> ChatWorkspace:
    ctx = resolve_effective_context(project=project, user=user, session_overrides=session_overrides)

    return ChatWorkspace.objects.create(
        project=project,
        folder=folder,
        title=title,
        created_by=user,
        resolved_context=ctx,
    )
