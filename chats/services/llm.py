# chats/services/llm.py
# Replace WHOLE FILE with this.

from __future__ import annotations

import json
import base64
from typing import Dict, List, Optional
from openai import OpenAI
from config.models import SystemConfigPointers


client = OpenAI()


# Assumes you already have:
# client = OpenAI()
# and ChatAttachment is your model with FileField `file` and `content_type`.


def _attachment_to_data_url(att) -> str:
    """
    Convert a Django FileField attachment into a base64 data URL.
    Option B: private images, no public URL required.
    """
    mime = (getattr(att, "content_type", "") or "image/png").strip().lower()
    if not mime.startswith("image/"):
        mime = "image/png"

    f = getattr(att, "file", None)
    if not f:
        raise ValueError("Attachment has no file.")

    f.open("rb")
    try:
        data = f.read()
    finally:
        f.close()

    b64 = base64.b64encode(data).decode("ascii")
    return "data:%s;base64,%s" % (mime, b64)

def _get_default_model_key() -> str:
    p = SystemConfigPointers.objects.first()
    return (getattr(p, "openai_model_default", "") or "gpt-5.1").strip()


def build_image_parts_from_attachments(attachments) -> List[Dict[str, str]]:
    """
    Build Responses API image parts: [{"type":"input_image","image_url":"data:..."}]
    """
    parts: List[Dict[str, str]] = []
    for att in attachments:
        ctype = (getattr(att, "content_type", "") or "").lower()
        if not ctype.startswith("image/"):
            continue
        parts.append(
            {
                "type": "input_image",
                "image_url": _attachment_to_data_url(att),
            }
        )
    return parts


from typing import Dict, List, Optional

def generate_panes(
    user_text: str,
    image_parts: Optional[List[Dict[str, str]]] = None,
    system_blocks: Optional[List[str]] = None,
    force_model: Optional[str] = None,
) -> Dict[str, str]:
    image_parts = image_parts or []
    system_blocks = system_blocks or []

    model = (force_model or _get_default_model_key()).strip()


    system_contract = (
        "Return JSON with keys:\n"
        "- answer: direct response\n"
        "- key_info: bullets / anchors\n"
        "- visuals: emojis, steps, breadcrumbs, ASCII diagrams\n"
        "- reasoning: reasoning summary\n"
        "- output: extractable artefact text\n"
    )
    ALLOWED_MODELS = [
        "gpt-5.1", "gpt-5-mini", "gpt-5-nano",
        "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
        "o3", "o4-mini", "gpt-4o",
    ]

   # model = _get_default_model_key()

    # Multiple system messages are fine.
    input_msgs = [{"role": "system", "content": system_contract}]
    for block in system_blocks:
        if block:
            input_msgs.append({"role": "system", "content": block})

    input_msgs.append(
        {
            "role": "user",
            "content": [{"type": "input_text", "text": user_text}] + image_parts,
        }
    )

    response = client.responses.create(
        model=model,
        input=input_msgs,
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

    try:
        parsed = response.output[0].content[0].parsed  # type: ignore[attr-defined]
        if isinstance(parsed, dict):
            return {
                "answer": parsed.get("answer", "") or "",
                "key_info": parsed.get("key_info", "") or "",
                "visuals": parsed.get("visuals", "") or "",
                "reasoning": parsed.get("reasoning", "") or "",
                "output": parsed.get("output", "") or "",
            }
    except Exception:
        pass

    import json
    payload = json.loads(response.output_text or "{}")
    return {
        "answer": payload.get("answer", "") or "",
        "key_info": payload.get("key_info", "") or "",
        "visuals": payload.get("visuals", "") or "",
        "reasoning": payload.get("reasoning", "") or "",
        "output": payload.get("output", "") or "",
    }


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
        model= _get_default_model_key(),
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
        model= _get_default_model_key(),
        input=[
            *[{"role": "system", "content": b} for b in system_blocks],
            *messages,
        ],
    )
    return (response.output_text or "").strip()
