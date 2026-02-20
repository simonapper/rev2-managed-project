# -*- coding: utf-8 -*-
# chats/services/workspace.py

from __future__ import annotations

from django.db import transaction

from projects.context import resolve_effective_context
from chats.models import ChatWorkspace
from chats.services.chat_bootstrap import bootstrap_chat


@transaction.atomic
def create_chat(
    *,
    project,
    user,
    title: str,
    folder=None,
    session_overrides: dict | None = None,
) -> ChatWorkspace:
    # Delegate all chat creation to the canonical bootstrap
    chat, _ = bootstrap_chat(
        project=project,
        user=user,
        title=title,
    )
    return chat
