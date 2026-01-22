# -*- coding: utf-8 -*-
# chats/services/cleanup.py

from django.db.models import Count, Q
from chats.models import ChatWorkspace, ChatMessage
from projects.models import Project


def delete_empty_sandbox_chats(*, project: Project) -> int:
    """
    Delete chats in a SANDBOX project that contain only SYSTEM messages.
    Returns number of chats deleted.
    """
    assert project.kind == "SANDBOX"

    chats = (
        ChatWorkspace.objects
        .filter(project=project)
        .annotate(
            non_system_count=Count(
                "messages",
                filter=~Q(messages__role="SYSTEM"),
            )
        )
        .filter(non_system_count=0)
    )

    count = chats.count()
    chats.delete()
    return count
