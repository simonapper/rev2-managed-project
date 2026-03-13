# imports/chatgpt_export_parser.py
# -*- coding: utf-8 -*-

from typing import List, Dict


def linearise_conversation(conv: dict) -> list[dict]:
    """
    Flatten a ChatGPT export conversation (mapping graph) into a chronological list.

    Returns list of dicts:
      { "role": "user|assistant|system|tool", "text": str, "create_time": float|None, "node_id": str }
    """
    mapping = conv.get("mapping") or {}
    if not isinstance(mapping, dict):
        return []

    out = []

    for node_id, node in mapping.items():
        if not isinstance(node, dict):
            continue

        msg = node.get("message")
        if not isinstance(msg, dict):
            continue

        author = msg.get("author") or {}
        role = (author.get("role") or "unknown").lower()

        content = msg.get("content") or {}
        parts = content.get("parts") or []

        if isinstance(parts, list):
            text = "\n".join([p for p in parts if isinstance(p, str)]).strip()
        else:
            text = ""

        if not text:
            continue

        # Hide tool/memory noise for MVP preview/import
        if role == "tool":
            continue
        if "Model set context updated." in text:
            continue
        if role == "assistant" and text.startswith("User's "):
            continue

        ct = msg.get("create_time")
        try:
            ct = float(ct) if ct is not None else None
        except Exception:
            ct = None

        out.append(
            {
                "node_id": str(node_id),
                "role": role,
                "text": text,
                "create_time": ct,
            }
        )

    out.sort(key=lambda m: (m["create_time"] is None, m["create_time"] or 0.0))
    return out

def group_into_turns(messages_flat: list[dict]) -> list[dict]:
    """
    Group flat messages into turns:
      - each USER message starts a new turn
      - subsequent non-user messages go into followups
    Adds:
      - assistants: subset of followups where role == 'assistant'
    """
    turns: list[dict] = []
    current: dict | None = None

    for m in (messages_flat or []):
        role = (m.get("role") or "").lower()
        text = (m.get("text") or "").strip()
        if not text:
            continue

        if role == "user":
            current = {"user": m, "followups": [], "assistants": []}
            turns.append(current)
            continue

        if current is None:
            current = {"user": None, "followups": [], "assistants": []}
            turns.append(current)

        current["followups"].append(m)
        if role == "assistant":
            current["assistants"].append(m)

    return turns
