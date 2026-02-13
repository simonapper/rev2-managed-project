# chats/services/llm.py

from __future__ import annotations

import base64
import importlib.util
import json
import os
import anthropic
from typing import Any, Dict, List, Optional

from config.models import SystemConfigPointers


_OPENAI_CLIENT = None
_ANTHROPIC_CLIENT = None
_ALLOWED_PROVIDERS = {"openai", "anthropic", "copilot"}
_PANE_KEYS = ("answer", "key_info", "visuals", "reasoning", "output")
_COPILOT_SPEC_OK: Optional[bool] = None


def _normalise_provider(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    return v if v in _ALLOWED_PROVIDERS else ""


def _copilot_available() -> bool:
    global _COPILOT_SPEC_OK
    if _COPILOT_SPEC_OK is None:
        try:
            _COPILOT_SPEC_OK = importlib.util.find_spec("myproject.copilot.agent") is not None
        except Exception:
            _COPILOT_SPEC_OK = False
    return bool(_COPILOT_SPEC_OK)


def _resolve_provider_candidate(candidate: str, *, strict: bool) -> str:
    if candidate != "copilot":
        return candidate
    if _copilot_available():
        return "copilot"
    if strict:
        raise ValueError("copilot provider is not available: missing module myproject.copilot.agent")
    return "openai"


def _resolve_provider(*, provider: Optional[str] = None, user: Any = None) -> str:
    if provider is not None:
        p = _normalise_provider(provider)
        if not p:
            raise ValueError("Unsupported LLM provider: " + str(provider))
        return _resolve_provider_candidate(p, strict=True)

    profile = getattr(user, "profile", None) if user is not None else None
    profile_provider = _normalise_provider(getattr(profile, "llm_provider", ""))
    if profile_provider:
        return _resolve_provider_candidate(profile_provider, strict=False)

    env_provider = _normalise_provider(os.getenv("LLM_PROVIDER", ""))
    if env_provider:
        return _resolve_provider_candidate(env_provider, strict=False)

    return "openai"


def _get_openai_client():
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None:
        from openai import OpenAI

        _OPENAI_CLIENT = OpenAI()
    return _OPENAI_CLIENT


def _get_anthropic_client():
    global _ANTHROPIC_CLIENT
    if _ANTHROPIC_CLIENT is None:
        import anthropic

        _ANTHROPIC_CLIENT = anthropic.Anthropic()
    return _ANTHROPIC_CLIENT


def _get_copilot_agent():
    if not _copilot_available():
        raise ValueError("copilot provider is not available: missing module myproject.copilot.agent")
    from myproject.copilot.agent import copilot

    return copilot


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks: List[str] = []
        for part in content:
            if isinstance(part, dict):
                text = str(part.get("text") or "").strip()
                if text:
                    chunks.append(text)
            elif part is not None:
                text = str(part).strip()
                if text:
                    chunks.append(text)
        return "\n".join(chunks).strip()
    if content is None:
        return ""
    return str(content).strip()


def _flatten_prompt(*, system_blocks: List[str], messages: List[Dict[str, Any]], user_text: str) -> str:
    lines: List[str] = []
    for block in system_blocks:
        b = (block or "").strip()
        if b:
            lines.append("SYSTEM:\n" + b)
    for msg in messages:
        role = str(msg.get("role") or "user").strip().upper()
        text = _content_to_text(msg.get("content"))
        if text:
            lines.append(role + ":\n" + text)
    u = (user_text or "").strip()
    if u:
        lines.append("USER:\n" + u)
    return "\n\n".join(lines).strip()


def _coerce_pane_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        lines: List[str] = []
        for item in value:
            if isinstance(item, str):
                lines.append("- " + item)
            else:
                lines.append("- " + json.dumps(item, ensure_ascii=True))
        return "\n".join(lines)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=True, indent=2)
    return str(value)


def _extract_json_dict_from_text(raw_text: str) -> Optional[Dict[str, Any]]:
    text = (raw_text or "").strip()
    if not text:
        return None

    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    if "```" in text:
        parts = text.split("```")
        for chunk in parts[1::2]:
            candidate = chunk.strip()
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                continue

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return None


def _normalise_panes_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    return {k: _coerce_pane_value(payload.get(k)) for k in _PANE_KEYS}


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


def _get_default_model_key(*, user: Any = None) -> str:
    profile = getattr(user, "profile", None) if user is not None else None
    user_value = (getattr(profile, "openai_model_default", "") or "").strip()
    if user_value:
        return user_value
    p = SystemConfigPointers.objects.first()
    return (getattr(p, "openai_model_default", "") or "gpt-5.1").strip()


def _get_default_anthropic_model_key(*, user: Any = None) -> str:
    profile = getattr(user, "profile", None) if user is not None else None
    user_value = (getattr(profile, "anthropic_model_default", "") or "").strip()
    if user_value:
        return user_value
    p = SystemConfigPointers.objects.first()
    config_value = getattr(p, "anthropic_model_default", "") if p is not None else ""
    env_value = os.getenv("ANTHROPIC_MODEL", "")
    return (config_value or env_value or "claude-sonnet-4-5-20250929").strip()


def _resolve_anthropic_model(force_model: Optional[str], *, user: Any = None) -> str:
    fm = (force_model or "").strip()
    if fm.startswith("claude-"):
        return fm
    return _get_default_anthropic_model_key(user=user)


def _data_url_to_anthropic_image_block(data_url: str) -> Optional[Dict[str, Any]]:
    prefix = "data:"
    marker = ";base64,"
    if not data_url.startswith(prefix):
        return None
    if marker not in data_url:
        return None
    media_type, b64 = data_url[len(prefix) :].split(marker, 1)
    media_type = media_type.strip().lower()
    b64 = b64.strip()
    if not media_type or not b64:
        return None
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": b64},
    }


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


def generate_panes(
    user_text: str,
    image_parts: Optional[List[Dict[str, str]]] = None,
    system_blocks: Optional[List[str]] = None,
    history_messages: Optional[List[Dict[str, Any]]] = None,
    force_model: Optional[str] = None,
    user: Any = None,
    provider: Optional[str] = None,
) -> Dict[str, str]:
    image_parts = image_parts or []
    system_blocks = system_blocks or []
    history_messages = history_messages or []

    model = (force_model or _get_default_model_key(user=user)).strip()
    selected_provider = _resolve_provider(provider=provider, user=user)

    system_contract = (
        "Return JSON with keys:\n"
        "- answer: direct response\n"
        "- key_info: bullets / anchors\n"
        "- visuals: emojis, steps, breadcrumbs, ASCII diagrams\n"
        "- reasoning: reasoning summary\n"
        "- output: extractable artefact text\n"
    )

    if selected_provider == "copilot":
        if image_parts:
            raise ValueError("copilot provider does not support image_parts")
        copilot_contract = system_contract + "\nReturn strict JSON only. No markdown. No prose outside JSON."
        prompt = _flatten_prompt(
            system_blocks=[copilot_contract] + system_blocks,
            messages=history_messages,
            user_text=user_text,
        )
        raw_text = (getattr(_get_copilot_agent().run(prompt), "text", "") or "").strip()
        payload = _extract_json_dict_from_text(raw_text)
        if payload is None:
            return {
                "answer": raw_text,
                "key_info": "",
                "visuals": "",
                "reasoning": "",
                "output": "",
            }
        return _normalise_panes_payload(payload)

    if selected_provider == "anthropic":
        system_text = "\n\n".join(
            [
                system_contract,
                *[b for b in system_blocks if b],
                "Return strict JSON only. No markdown. No prose outside JSON.",
            ]
        ).strip()

        anthropic_messages: List[Dict[str, Any]] = []
        for msg in history_messages:
            if not msg:
                continue
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            text = _content_to_text(msg.get("content"))
            if text:
                anthropic_messages.append({"role": role, "content": text})

        user_content: List[Dict[str, Any]] = [{"type": "text", "text": user_text}]
        for part in image_parts:
            image_url = str(part.get("image_url") or "")
            image_block = _data_url_to_anthropic_image_block(image_url)
            if image_block:
                user_content.append(image_block)

        anthropic_messages.append({"role": "user", "content": user_content})

        response = _get_anthropic_client().messages.create(
            model=_resolve_anthropic_model(force_model, user=user),
            max_tokens=2048,
            system=system_text,
            messages=anthropic_messages,
        )
        raw_text = "\n".join(
            [
                getattr(block, "text", "")
                for block in (response.content or [])
                if getattr(block, "type", "") == "text"
            ]
        ).strip()

        payload = _extract_json_dict_from_text(raw_text)
        if payload is None:
            return {
                "answer": raw_text,
                "key_info": "",
                "visuals": "",
                "reasoning": "",
                "output": "",
            }
        return _normalise_panes_payload(payload)

    input_msgs: List[Dict[str, Any]] = [{"role": "system", "content": system_contract}]
    for block in system_blocks:
        if block:
            input_msgs.append({"role": "system", "content": block})

    for msg in history_messages:
        if not msg:
            continue
        if msg.get("role") not in ("user", "assistant"):
            continue
        if not msg.get("content"):
            continue
        input_msgs.append(msg)

    input_msgs.append(
        {
            "role": "user",
            "content": [{"type": "input_text", "text": user_text}] + image_parts,
        }
    )

    client = _get_openai_client()
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

    payload = json.loads(response.output_text or "{}")
    return {
        "answer": payload.get("answer", "") or "",
        "key_info": payload.get("key_info", "") or "",
        "visuals": payload.get("visuals", "") or "",
        "reasoning": payload.get("reasoning", "") or "",
        "output": payload.get("output", "") or "",
    }


def generate_handshake(
    *,
    system_blocks: List[str],
    first_name: Optional[str],
    user: Any = None,
    provider: Optional[str] = None,
) -> str:
    """
    Proof-of-wiring handshake.
    Sends SYSTEM blocks to the LLM and forces a deterministic greeting.
    """
    name = (first_name or "").strip()

    if name:
        greeting = f"Hello {name} - I'm ready."
    else:
        greeting = "Hello - I'm ready."

    blocks = list(system_blocks)
    blocks.append(f'Your response should be exactly: "{greeting}"')

    selected_provider = _resolve_provider(provider=provider, user=user)
    if selected_provider == "copilot":
        prompt = _flatten_prompt(
            system_blocks=blocks,
            messages=[],
            user_text="Bootstrap handshake. Respond now.",
        )
        return (getattr(_get_copilot_agent().run(prompt), "text", "") or "").strip()

    if selected_provider == "anthropic":
        response = _get_anthropic_client().messages.create(
            model=_get_default_anthropic_model_key(user=user),
            max_tokens=128,
            system="\n\n".join(blocks),
            messages=[{"role": "user", "content": "Bootstrap handshake. Respond now."}],
        )
        return "\n".join(
            [
                getattr(block, "text", "")
                for block in (response.content or [])
                if getattr(block, "type", "") == "text"
            ]
        ).strip()

    client = _get_openai_client()
    response = client.responses.create(
        model=_get_default_model_key(user=user),
        input=[
            *[{"role": "system", "content": block} for block in blocks],
            {"role": "user", "content": "Bootstrap handshake. Respond now."},
        ],
    )

    return (response.output_text or "").strip()


def generate_text(
    *,
    system_blocks: list[str],
    messages: list[dict],
    user: Any = None,
    provider: Optional[str] = None,
) -> str:
    """
    Plain text generation for normal turns.
    system_blocks: resolved SYSTEM instructions (strings)
    messages: [{'role': 'user'|'assistant', 'content': str}, ...]
    """
    selected_provider = _resolve_provider(provider=provider, user=user)
    if selected_provider == "copilot":
        prompt = _flatten_prompt(
            system_blocks=system_blocks,
            messages=messages,
            user_text="",
        )
        return (getattr(_get_copilot_agent().run(prompt), "text", "") or "").strip()

    if selected_provider == "anthropic":
        clean_messages: List[Dict[str, str]] = []
        for msg in messages:
            if not msg:
                continue
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            text = _content_to_text(msg.get("content"))
            if text:
                clean_messages.append({"role": role, "content": text})
        response = _get_anthropic_client().messages.create(
            model=_get_default_anthropic_model_key(user=user),
            max_tokens=2048,
            system="\n\n".join([b for b in system_blocks if b]).strip(),
            messages=clean_messages,
        )
        return "\n".join(
            [
                getattr(block, "text", "")
                for block in (response.content or [])
                if getattr(block, "type", "") == "text"
            ]
        ).strip()

    client = _get_openai_client()
    response = client.responses.create(
        model=_get_default_model_key(user=user),
        input=[
            *[{"role": "system", "content": b} for b in system_blocks],
            *messages,
        ],
    )
    return (response.output_text or "").strip()
