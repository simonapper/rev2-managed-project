# -*- coding: utf-8 -*-
# chats/permissions.py

from __future__ import annotations

from projects.services_project_membership import is_project_manager
from chats.models import ChatWorkspace

def can_read_chat(*, chat: ChatWorkspace, user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser or user.is_staff:
        return True

    if chat.created_by_id == user.id:
        return True

    scope = chat.project.policy.chat_read_scope

    if scope == "OWNER_ONLY":
        return False

    if scope == "PROJECT_MANAGERS":
        return is_project_manager(chat.project, user)

    if scope == "ANY_MANAGER":
        # Minimal definition for now:
        return is_project_manager(chat.project, user)  # OR implement global manager later

    return False
