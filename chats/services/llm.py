# chats/services/llm.py

from __future__ import annotations

import base64
import importlib.util
import json
import logging
import os
import re
import urllib.request
import anthropic
from typing import Any, Callable, Dict, List, Optional

from django.conf import settings

from chats.services.contracts.pipeline import ContractContext, build_system_blocks
from chats.services.derax.compile import compile_derax_chat_run_to_cko_artefact
from chats.services.derax.envelope import build_derax_system_blocks
from chats.services.derax.persist import persist_derax_payload
from chats.services.derax.validate import build_correction_message, validate_derax_text
from config.models import SystemConfigPointers


_OPENAI_CLIENT = None
_ANTHROPIC_CLIENT = None
_DEEPSEEK_CLIENT = None
_ALLOWED_PROVIDERS = {"openai", "anthropic", "deepseek", "copilot"}
_PANE_KEYS = ("answer", "key_info", "visuals", "reasoning", "output")
_COPILOT_SPEC_OK: Optional[bool] = None
_LOGGER = logging.getLogger(__name__)


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


def generate_openai_image_bytes(
    *,
    prompt: str,
    model: Optional[str] = None,
    size: str = "1024x1024",
) -> Dict[str, Any]:
    """
    Generate one image using OpenAI Images API and return raw bytes.
    """
    text = (prompt or "").strip()
    if not text:
        raise ValueError("Image prompt is empty")

    image_model = (model or os.getenv("OPENAI_IMAGE_MODEL", "") or "gpt-image-1").strip()
    client = _get_openai_client()

    try:
        response = client.images.generate(
            model=image_model,
            prompt=text,
            size=size,
            response_format="b64_json",
        )
    except Exception:
        response = client.images.generate(
            model=image_model,
            prompt=text,
            size=size,
        )

    data = getattr(response, "data", None)
    if not data and isinstance(response, dict):
        data = response.get("data")
    if not data:
        raise ValueError("Image API returned no data")

    first = data[0]
    b64 = ""
    image_url = ""
    file_id = ""
    if isinstance(first, dict):
        b64 = str(first.get("b64_json") or "").strip()
        image_url = str(first.get("url") or "").strip()
        file_id = str(first.get("file_id") or "").strip()
    else:
        b64 = str(getattr(first, "b64_json", "") or "").strip()
        image_url = str(getattr(first, "url", "") or "").strip()
        file_id = str(getattr(first, "file_id", "") or "").strip()

    image_bytes = b""
    if b64:
        try:
            image_bytes = base64.b64decode(b64, validate=True)
        except Exception as exc:
            raise ValueError("Image payload decode failed") from exc
    elif image_url:
        try:
            with urllib.request.urlopen(image_url, timeout=20) as resp:
                image_bytes = bytes(resp.read() or b"")
        except Exception as exc:
            raise ValueError("Image API returned URL but download failed") from exc
    else:
        raise ValueError("Image API returned no b64 payload")

    if not image_bytes:
        raise ValueError("Image payload was empty")

    return {
        "image_bytes": image_bytes,
        "mime_type": "image/png",
        "model": image_model,
        "file_id": file_id,
    }


def _get_anthropic_client():
    global _ANTHROPIC_CLIENT
    if _ANTHROPIC_CLIENT is None:
        import anthropic

        _ANTHROPIC_CLIENT = anthropic.Anthropic()
    return _ANTHROPIC_CLIENT


def _get_deepseek_client():
    global _DEEPSEEK_CLIENT
    if _DEEPSEEK_CLIENT is None:
        from deepseek import DeepSeekClient

        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        _DEEPSEEK_CLIENT = DeepSeekClient(api_key=api_key)
    return _DEEPSEEK_CLIENT


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
    loose = _extract_loose_pane_payload(text)
    if loose:
        return loose
    return None


def _extract_loose_pane_payload(raw_text: str) -> Optional[Dict[str, Any]]:
    """
    Best-effort parser for JSON-like pane payloads that are not valid JSON
    (for example raw newlines inside quoted strings).
    """
    text = (raw_text or "").strip()
    if not text:
        return None

    key_pat = re.compile(r'"(answer|key_info|visuals|reasoning|output)"\s*:\s*', re.IGNORECASE)
    matches = list(key_pat.finditer(text))
    if not matches:
        return None

    out: Dict[str, Any] = {}
    for idx, match in enumerate(matches):
        key = (match.group(1) or "").lower()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        raw_value = text[start:end].strip()
        if not raw_value:
            out[key] = ""
            continue

        if idx + 1 < len(matches):
            if raw_value.endswith(","):
                raw_value = raw_value[:-1].rstrip()
        else:
            raw_value = re.sub(r"\s*}\s*$", "", raw_value, flags=re.DOTALL).rstrip()
            if raw_value.endswith(","):
                raw_value = raw_value[:-1].rstrip()

        if raw_value.startswith('"'):
            str_val = raw_value[1:]
            if str_val.endswith('"'):
                str_val = str_val[:-1]
            str_val = (
                str_val
                .replace('\\"', '"')
                .replace("\\n", "\n")
                .replace("\\r", "\r")
                .replace("\\t", "\t")
                .replace("\\\\", "\\")
            )
            out[key] = str_val.strip()
            continue

        if raw_value.startswith("["):
            try:
                out[key] = json.loads(raw_value)
                continue
            except Exception:
                items = re.findall(r'"([^"]+)"', raw_value, flags=re.DOTALL)
                if items:
                    out[key] = [i.strip() for i in items if i.strip()]
                    continue

        if raw_value.startswith("{"):
            try:
                out[key] = json.loads(raw_value)
                continue
            except Exception:
                out[key] = raw_value
                continue

        out[key] = raw_value.strip()

    if not any(str(out.get(k) or "").strip() for k in _PANE_KEYS):
        return None
    return out


def _normalise_panes_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    return {k: _coerce_pane_value(payload.get(k)) for k in _PANE_KEYS}


def _all_panes_empty(panes: Dict[str, Any]) -> bool:
    for k in _PANE_KEYS:
        if str(panes.get(k) or "").strip():
            return False
    return True


def _openai_response_fallback_text(response: Any) -> str:
    chunks: List[str] = []
    primary = str(getattr(response, "output_text", "") or "").strip()
    if primary:
        chunks.append(primary)

    for item in (getattr(response, "output", None) or []):
        for content in (getattr(item, "content", None) or []):
            text = str(getattr(content, "text", "") or "").strip()
            if text:
                chunks.append(text)

    seen = set()
    out: List[str] = []
    for c in chunks:
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    return "\n\n".join(out).strip()


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


def _get_default_deepseek_model_key(*, user: Any = None) -> str:
    profile = getattr(user, "profile", None) if user is not None else None
    user_value = (getattr(profile, "deepseek_model_default", "") or "").strip()
    if user_value:
        return user_value
    env_value = os.getenv("DEEPSEEK_MODEL", "")
    return (env_value or "deepseek-chat").strip()


def _resolve_deepseek_text(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if choices:
        msg = getattr(choices[0], "message", None)
        content = getattr(msg, "content", None)
        text = _content_to_text(content)
        if text:
            return text
        reasoning = getattr(msg, "reasoning_content", None)
        return _content_to_text(reasoning)
    if isinstance(response, dict):
        try:
            msg = response["choices"][0]["message"]
            text = _content_to_text(msg.get("content"))
            if text:
                return text
            return _content_to_text(msg.get("reasoning_content"))
        except Exception:
            return ""
    return ""


def _deepseek_chat_completion(*, messages: List[Dict[str, Any]], model: str) -> Any:
    client = _get_deepseek_client()
    try:
        return client.chat_completion(model=model, messages=messages)
    except TypeError:
        return client.chat_completion(messages=messages)


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


def _call_llm_raw_text(
    *,
    selected_provider: str,
    model: str,
    system_blocks: list[str],
    history_messages: list[dict],
    user_text: str,
    image_parts: list[dict] | None,
    force_model: str | None,
    user: Any,
) -> str:
    image_parts = list(image_parts or [])

    if selected_provider == "copilot":
        if image_parts:
            raise ValueError("copilot provider does not support image_parts")
        prompt = _flatten_prompt(
            system_blocks=system_blocks,
            messages=history_messages,
            user_text=user_text,
        )
        return (getattr(_get_copilot_agent().run(prompt), "text", "") or "").strip()

    if selected_provider == "anthropic":
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
            max_tokens=4096,
            system="\n\n".join([b for b in system_blocks if b]).strip(),
            messages=anthropic_messages,
        )
        return "\n".join(
            [
                getattr(block, "text", "")
                for block in (response.content or [])
                if getattr(block, "type", "") == "text"
            ]
        ).strip()

    if selected_provider == "deepseek":
        if image_parts:
            raise ValueError("deepseek provider does not support image_parts")
        prompt = _flatten_prompt(
            system_blocks=system_blocks,
            messages=history_messages,
            user_text=user_text,
        )
        response = _deepseek_chat_completion(
            model=_get_default_deepseek_model_key(user=user),
            messages=[{"role": "user", "content": prompt}],
        )
        return _resolve_deepseek_text(response).strip()

    input_msgs: List[Dict[str, Any]] = []
    for block in system_blocks:
        if block:
            input_msgs.append({"role": "system", "content": block})
    for msg in history_messages:
        if not msg:
            continue
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content")
        if not content:
            continue
        input_msgs.append({"role": role, "content": content})
    input_msgs.append(
        {
            "role": "user",
            "content": [{"type": "input_text", "text": user_text}] + image_parts,
        }
    )
    response = _get_openai_client().responses.create(
        model=model,
        input=input_msgs,
    )
    return _openai_response_fallback_text(response).strip()


def generate_panes(
    user_text: str,
    image_parts: Optional[List[Dict[str, str]]] = None,
    system_blocks: Optional[List[str]] = None,
    history_messages: Optional[List[Dict[str, Any]]] = None,
    force_model: Optional[str] = None,
    user: Any = None,
    provider: Optional[str] = None,
    work_item: Any = None,
    contract_ctx: Optional[ContractContext] = None,
) -> Dict[str, str]:
    image_parts = image_parts or []
    system_blocks = list(system_blocks or [])
    history_messages = history_messages or []
    pipeline_enabled = bool(getattr(settings, "CONTRACT_PIPELINE_ENABLED", True))
    contract_trace: dict = {}
    if pipeline_enabled:
        if contract_ctx is None:
            contract_ctx = ContractContext(
                user=user,
                work_item=work_item,
                user_text=user_text,
                legacy_system_blocks=system_blocks,
                is_cde=False,
            )
        system_blocks, contract_trace = build_system_blocks(contract_ctx)
        if settings.DEBUG:
            _LOGGER.debug("contracts.pipeline.generate_panes trace=%s", contract_trace)

    model = (force_model or _get_default_model_key(user=user)).strip()
    selected_provider = _resolve_provider(provider=provider, user=user)
    system_contract = ""
    if not pipeline_enabled:
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
        copilot_contract = (
            system_contract + "\nReturn strict JSON only. No markdown. No prose outside JSON."
            if system_contract else ""
        )
        prompt = _flatten_prompt(
            system_blocks=([copilot_contract] if copilot_contract else []) + system_blocks,
            messages=history_messages,
            user_text=user_text,
        )
        raw_text = (getattr(_get_copilot_agent().run(prompt), "text", "") or "").strip()
        payload = _extract_json_dict_from_text(raw_text)
        if payload is None:
            if not raw_text:
                raw_text = "[copilot] empty text response"
            return {
                "answer": raw_text,
                "key_info": "",
                "visuals": "",
                "reasoning": "",
                "output": "",
            }
        panes = _normalise_panes_payload(payload)
        if _all_panes_empty(panes):
            panes["answer"] = raw_text or "[copilot] empty pane payload"
        return panes

    if selected_provider == "anthropic":
        if system_contract:
            system_text = "\n\n".join(
                [
                    system_contract,
                    *[b for b in system_blocks if b],
                    "Return strict JSON only. No markdown. No prose outside JSON.",
                ]
            ).strip()
        else:
            system_text = "\n\n".join([b for b in system_blocks if b]).strip()

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
            if not raw_text:
                raw_text = "[anthropic] empty text response"
            return {
                "answer": raw_text,
                "key_info": "",
                "visuals": "",
                "reasoning": "",
                "output": "",
            }
        panes = _normalise_panes_payload(payload)
        if _all_panes_empty(panes):
            panes["answer"] = raw_text or "[anthropic] empty pane payload"
        return panes

    if selected_provider == "deepseek":
        if image_parts:
            raise ValueError("deepseek provider does not support image_parts")

        prompt = _flatten_prompt(
            system_blocks=(
                [system_contract, *system_blocks, "Return strict JSON only. No markdown. No prose outside JSON."]
                if system_contract
                else system_blocks
            ),
            messages=history_messages,
            user_text=user_text,
        )
        response = _deepseek_chat_completion(
            model=_get_default_deepseek_model_key(user=user),
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = _resolve_deepseek_text(response).strip()
        payload = _extract_json_dict_from_text(raw_text)
        if payload is None:
            if not raw_text:
                raw_text = "[deepseek] empty text response"
            return {
                "answer": raw_text,
                "key_info": "",
                "visuals": "",
                "reasoning": "",
                "output": "",
            }
        panes = _normalise_panes_payload(payload)
        if _all_panes_empty(panes):
            panes["answer"] = raw_text or "[deepseek] empty pane payload"
        return panes

    input_msgs: List[Dict[str, Any]] = []
    if system_contract:
        input_msgs.append({"role": "system", "content": system_contract})
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
            panes = {
                "answer": parsed.get("answer", "") or "",
                "key_info": parsed.get("key_info", "") or "",
                "visuals": parsed.get("visuals", "") or "",
                "reasoning": parsed.get("reasoning", "") or "",
                "output": parsed.get("output", "") or "",
            }
            if not _all_panes_empty(panes):
                return panes
    except Exception:
        pass

    raw_text = _openai_response_fallback_text(response)
    payload = _extract_json_dict_from_text(raw_text)
    if payload is not None:
        panes = _normalise_panes_payload(payload)
        if not _all_panes_empty(panes):
            return panes

    if not raw_text:
        raw_text = "[openai] empty response payload"
    return {
        "answer": raw_text,
        "key_info": "",
        "visuals": "",
        "reasoning": "",
        "output": "",
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

    if selected_provider == "deepseek":
        prompt = _flatten_prompt(
            system_blocks=blocks,
            messages=[],
            user_text="Bootstrap handshake. Respond now.",
        )
        response = _deepseek_chat_completion(
            model=_get_default_deepseek_model_key(user=user),
            messages=[{"role": "user", "content": prompt}],
        )
        return _resolve_deepseek_text(response).strip()

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
    contract_ctx: Optional[ContractContext] = None,
) -> str:
    """
    Plain text generation for normal turns.
    system_blocks: resolved SYSTEM instructions (strings)
    messages: [{'role': 'user'|'assistant', 'content': str}, ...]
    """
    selected_provider = _resolve_provider(provider=provider, user=user)
    pipeline_enabled = bool(getattr(settings, "CONTRACT_PIPELINE_ENABLED", True))
    contract_trace: dict = {}
    if pipeline_enabled:
        if contract_ctx is None:
            contract_ctx = ContractContext(
                user=user,
                user_text="",
                legacy_system_blocks=list(system_blocks or []),
                include_envelope=False,
            )
        system_blocks, contract_trace = build_system_blocks(contract_ctx)
        if settings.DEBUG:
            _LOGGER.debug("contracts.pipeline.generate_text trace=%s", contract_trace)
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

    if selected_provider == "deepseek":
        prompt = _flatten_prompt(
            system_blocks=system_blocks,
            messages=messages,
            user_text="",
        )
        response = _deepseek_chat_completion(
            model=_get_default_deepseek_model_key(user=user),
            messages=[{"role": "user", "content": prompt}],
        )
        return _resolve_deepseek_text(response).strip()

    client = _get_openai_client()
    response = client.responses.create(
        model=_get_default_model_key(user=user),
        input=[
            *[{"role": "system", "content": b} for b in system_blocks],
            *messages,
        ],
    )
    return (response.output_text or "").strip()


def generate_derax(
    *,
    user_text: str,
    phase: str,
    project_id: int,
    chat_id: int,
    turn_id: str,
    image_parts: Optional[list[dict[str, str]]] = None,
    system_blocks: Optional[list[str]] = None,
    history_messages: Optional[list[dict[str, Any]]] = None,
    force_model: Optional[str] = None,
    user: Any = None,
    provider: Optional[str] = None,
    work_item: Any = None,
    contract_ctx: Optional[ContractContext] = None,
    persist: bool = True,
    compile_after: bool = False,
    llm_raw_text_fn: Optional[Callable[..., str]] = None,
) -> dict[str, Any]:
    del contract_ctx
    image_parts = list(image_parts or [])
    base_system_blocks = list(system_blocks or [])
    history_messages = list(history_messages or [])

    model = (force_model or _get_default_model_key(user=user)).strip()
    selected_provider = _resolve_provider(provider=provider, user=user)
    derax_system_blocks = build_derax_system_blocks(base_system_blocks=base_system_blocks, phase=phase)

    call_fn = llm_raw_text_fn or _call_llm_raw_text
    latest_errors: list[str] = []
    payload: dict | None = None
    raw_text = ""
    loop_user_text = str(user_text or "")

    for _ in range(3):
        raw_text = call_fn(
            selected_provider=selected_provider,
            model=model,
            system_blocks=derax_system_blocks,
            history_messages=history_messages,
            user_text=loop_user_text,
            image_parts=image_parts,
            force_model=force_model,
            user=user,
        )
        ok, maybe_payload, errors = validate_derax_text(raw_text)
        if ok and isinstance(maybe_payload, dict):
            payload = maybe_payload
            latest_errors = []
            break
        latest_errors = list(errors or [])
        loop_user_text = build_correction_message(latest_errors)

    if payload is None:
        joined = "; ".join(latest_errors) if latest_errors else "Invalid DERAX output"
        raise ValueError("DERAX validation failed: " + joined)

    json_artefact_id = ""
    if persist:
        if work_item is not None:
            doc_or_id = persist_derax_payload(
                work_item=work_item,
                payload=payload,
                user=user,
                chat=None,
                turn_id=str(turn_id or ""),
                phase=str(phase or ""),
            )
            json_artefact_id = str(getattr(doc_or_id, "id", "") or "")
        else:
            doc_or_id = persist_derax_payload(
                project_id=int(project_id),
                chat_id=int(chat_id),
                turn_id=str(turn_id or ""),
                phase=str(phase or ""),
                payload=payload,
                raw_text=raw_text,
                user_id=getattr(user, "id", None),
            )
            json_artefact_id = str(doc_or_id or "")
        generated = list((payload.get("artefacts") or {}).get("generated") or [])
        generated.append(
            {
                "artefact_id": json_artefact_id,
                "kind": "DERAX_JSON",
                "title": f"{str(phase or '').strip().upper()} payload",
            }
        )
        payload.setdefault("artefacts", {})
        payload["artefacts"]["generated"] = generated

    out: dict[str, Any] = {
        "payload": payload,
        "json_artefact_id": json_artefact_id,
    }
    if compile_after:
        title = "DERAX Compiled CKO"
        if work_item is not None:
            title = str(getattr(work_item, "title", "") or title)
            compiled_id = compile_derax_chat_run_to_cko_artefact(
                project_id=int(getattr(work_item, "project_id", project_id)),
                chat_id=int(chat_id),
                title=title,
            )
        else:
            compiled_id = compile_derax_chat_run_to_cko_artefact(
                project_id=int(project_id),
                chat_id=int(chat_id),
                title=title,
            )
        out["compiled_artefact_id"] = str(compiled_id or "")
    return out
