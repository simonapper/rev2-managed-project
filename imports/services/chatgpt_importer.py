# -*- coding: utf-8 -*-
# imports/services/chatgpt_importer.py
from __future__ import annotations

from typing import Any, Dict, List
from uuid import uuid4

from django.utils import timezone
from chats.models import ChatWorkspace, ChatMessage


def import_chatgpt_json(data: Any, project, user) -> List[ChatWorkspace]:
    # Accept dict {"chats":[...]} or list [...]
    chats = data.get("chats", []) if isinstance(data, dict) else data
    if not isinstance(chats, list):
        raise ValueError("Structured import format not recognised: expected dict['chats'] or list.")

    workspaces: List[ChatWorkspace] = []

    for chat in chats:
        title = chat.get("title") or "Untitled Conversation"

        workspace = ChatWorkspace.objects.create(
            project=project,
            title=title,
            status=ChatWorkspace.Status.ACTIVE,
            created_by=user,
        )

        for turn in chat.get("turns", []):
            turn_id = uuid4().hex
            turn_index = turn.get("turn_index")

            user_text = (turn.get("user_input") or "").strip()
            if user_text:
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


            for r in turn.get("responses", []):
                text = (r.get("response_answer") or "").strip()
                if not text:
                    continue  # skip empty answers

                ChatMessage.objects.create(
                    chat=workspace,
                    role=ChatMessage.Role.ASSISTANT,
                    channel=ChatMessage.Channel.ANSWER,
                    content=text,
                    tool_metadata={
                        "turn_id": turn_id,
                        "import": "chatgpt_structured",
                        "turn_index": turn_index,
                        "response_index": r.get("response_index"),
                        "assistant_type": r.get("assistant_type"),
                        "sources": r.get("response_sources"),
                        "reasoning": r.get("response_reasoning"),
                        "commentary": r.get("response_commentary"),
                        "visuals": r.get("response_visuals"),
                        "uncategorised": r.get("response_uncategorised"),
                    },
                )

        workspaces.append(workspace)

    return workspaces
