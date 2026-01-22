# chats/services/llm.py
# Replace WHOLE FILE with this.

from __future__ import annotations

import json
from typing import Dict, List, Optional
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

def generate_handshake(*, system_blocks: List[str], first_name: Optional[str]) -> str:
    """
    Proof-of-wiring handshake.
    Sends SYSTEM blocks to the LLM and forces a deterministic greeting.
    """
    name = (first_name or "").strip()

    if name:
        greeting = f"Hello {name} - I'm ready."
    else:
        greeting = "Hello - I'm ready."

    # Append forced-response instruction as final SYSTEM block
    blocks = list(system_blocks)
    blocks.append(f'Your response should be exactly: "{greeting}"')

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            *[{"role": "system", "content": block} for block in blocks],
            {"role": "user", "content": "Bootstrap handshake. Respond now."},
        ],
    )

    return (response.output_text or "").strip()

def generate_text(*, system_blocks: list[str], messages: list[dict]) -> str:
    """
    Plain text generation for normal turns.
    system_blocks: resolved SYSTEM instructions (strings)
    messages: [{'role': 'user'|'assistant', 'content': str}, ...]
    """
    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            *[{"role": "system", "content": b} for b in system_blocks],
            *messages,
        ],
    )
    return (response.output_text or "").strip()
