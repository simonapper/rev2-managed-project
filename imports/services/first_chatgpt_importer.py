# -*- coding: utf-8 -*-
# imports/services/chatgpt_importer.py
from __future__ import annotations

from typing import List, Dict, Any
from uuid import uuid4

from django.utils import timezone

from chats.models import ChatWorkspace, ChatMessage


def import_chatgpt_json(data: List[Dict[str, Any]], project, user) -> List[ChatWorkspace]:
    """
    Import ChatGPT export into the project as ChatWorkspaces.
    Each conversation becomes a ChatWorkspace with messages.
    """
    workspaces: List[ChatWorkspace] = []

    # lazy import to avoid circulars
    from imports.chatgpt_export_parser import linearise_conversation, group_into_turns

    for conv in data:
        title = conv.get("title") or "Untitled Conversation"

        workspace = ChatWorkspace.objects.create(
            project=project,
            title=title,
            status=ChatWorkspace.Status.ACTIVE,   # keep it visible in normal lists
            created_by=user,
        )

        messages_flat = linearise_conversation(conv)
        turns = group_into_turns(messages_flat)

        for i, turn in enumerate(turns, start=1):
            turn_id = uuid4().hex

            u = turn.get("user")
            if u and (u.get("text") or "").strip():
                ChatMessage.objects.create(
                    chat=workspace,
                    role=ChatMessage.Role.ASSISTANT,
                    raw_text=text,

                    # minimal viable panes (safe default)
                    answer_text=text,
                    reasoning_text="",
                    output_text="",

                    segment_meta={
                        "source": "import",
                        "confidence": "LOW",
                        "parser": "importer_v1",
                    },
                )


            for m in (turn.get("followups") or []):
                text = (m.get("text") or "").strip()
                if not text:
                    continue

                role = (m.get("role") or "").lower()
                mapped_role = ChatMessage.Role.ASSISTANT
                if role == "system":
                    mapped_role = ChatMessage.Role.SYSTEM
                elif role == "tool":
                    mapped_role = ChatMessage.Role.TOOL

                ChatMessage.objects.create(
                    chat=workspace,
                    role=mapped_role,
                    channel=ChatMessage.Channel.ANSWER,  # MVP: everything into ANSWER
                    content=text,
                    tool_metadata={
                        "turn_id": turn_id,
                        "import": "chatgpt",
                        "turn_index": i,
                        "node_id": m.get("node_id"),
                    },
                )

        # update workspace “last output” fields (optional but nice)
        last_assistant = (
            ChatMessage.objects.filter(chat=workspace, role=ChatMessage.Role.ASSISTANT)
            .order_by("-created_at")
            .first()
        )
        if last_assistant:
            workspace.last_output_snippet = (last_assistant.content or "")[:280]
            workspace.last_output_at = timezone.now()
            workspace.save(update_fields=["last_output_snippet", "last_output_at", "updated_at"])

        workspaces.append(workspace)

    return workspaces
