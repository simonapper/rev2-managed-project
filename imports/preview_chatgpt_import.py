# -*- coding: utf-8 -*-
# imports/preview_chatgpt_import.py
# Purpose: Preview ChatGPT export conversations as turns (CLI)

import argparse
import json
from pathlib import Path
from datetime import datetime

from imports.chatgpt_export_parser import (
    linearise_conversation,
    group_into_turns,
)

SNIPPET_LEN = 120


def ts(t: float | None) -> str:
    if not t:
        return "?"
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


def load_export(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Export not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Export JSON is not a list of conversations")
    return data


def list_conversations(convs: list[dict]) -> None:
    for i, c in enumerate(convs):
        title = c.get("title") or "(untitled)"
        created = ts(c.get("create_time"))
        updated = ts(c.get("update_time"))
        print(f"[{i:>3}] {title}  ({created} → {updated})")


def preview_conversation(conv: dict, limit: int | None) -> None:
    messages = linearise_conversation(conv)
    turns = group_into_turns(messages)

    if not turns:
        print("No turns found.")
        return

    for i, t in enumerate(turns, start=1):
        if limit and i > limit:
            break

        print(f"\n--- TURN {i} ---")

        user = t.get("user")
        assistants = t.get("assistants") or []

        if user:
            print("USER:", (user.get("text") or "")[:SNIPPET_LEN])

        if assistants:
            print("ASSISTANT:", (assistants[0].get("text") or "")[:SNIPPET_LEN])

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview ChatGPT export conversations as turns"
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Path to ChatGPT export JSON file",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List conversations and exit",
    )
    parser.add_argument(
        "--conversation",
        type=int,
        default=0,
        help="Conversation index to preview (default: 0)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of turns shown",
    )

    args = parser.parse_args()

    export_path = Path(args.file)
    conversations = load_export(export_path)

    if args.list:
        list_conversations(conversations)
        return

    if args.conversation < 0 or args.conversation >= len(conversations):
        raise IndexError("Conversation index out of range")

    conv = conversations[args.conversation]

    title = conv.get("title") or "(untitled)"
    print(f"\n=== Conversation ===")
    print(f"Title: {title}")
    print(f"Created: {ts(conv.get('create_time'))}")
    print(f"Updated: {ts(conv.get('update_time'))}")

    preview_conversation(conv, args.limit)


if __name__ == "__main__":
    main()
