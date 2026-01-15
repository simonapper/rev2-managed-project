# chats/services/llm.py
# Replace WHOLE FILE with this.

from __future__ import annotations

import json
from typing import Dict

from openai import OpenAI

client = OpenAI()


def generate_panes(user_text: str) -> Dict[str, str]:
    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {
                "role": "system",
                "content": (
                    "Return JSON with keys:\n"
                    "- answer: direct response\n"
                    "- key_info: bullets / anchors\n"
                    "- visuals: emojis, steps, breadcrumbs, ASCII diagrams\n"
                    "- reasoning: reasoning summary\n"
                    "- output: extractable artefact text\n"
                ),
            },
            {"role": "user", "content": user_text},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "pane_payload",
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "answer": {"type": "string"},
                        "key_info": {"type": "string"},
                        "visuals": {"type": "string"},
                        "reasoning": {"type": "string"},
                        "output": {"type": "string"},
                    },
                    "required": ["answer", "key_info", "visuals", "reasoning", "output"],
                },
                "strict": True,
            }
        },
    )

    return json.loads(response.output_text)
