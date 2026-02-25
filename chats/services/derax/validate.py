# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from typing import Any

from chats.services.derax.phase_rules import check_required_nonempty
from chats.services.derax.schema import empty_payload, validate_structural


def extract_strict_json(text: str) -> tuple[dict | None, list[str]]:
    raw = str(text or "")
    stripped = raw.strip()
    if not stripped:
        return None, ["Invalid JSON: empty response"]
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None, ["Non-JSON content outside JSON object"]
    try:
        payload = json.loads(stripped)
    except Exception as exc:
        return None, [f"Invalid JSON: {exc}"]
    if not isinstance(payload, dict):
        return None, ["Invalid JSON: top-level value must be an object"]
    return payload, []


def validate_derax_text(text: str) -> tuple[bool, dict | None, list[str]]:
    payload, parse_errors = extract_strict_json(text)
    if parse_errors:
        return False, None, parse_errors

    assert payload is not None
    errors: list[str] = []

    schema_ok, schema_errors = validate_structural(payload)
    if not schema_ok:
        errors.extend(schema_errors)

    try:
        phase_ok, phase_errors = check_required_nonempty(payload)
    except Exception as exc:
        phase_ok, phase_errors = False, [str(exc)]
    if not phase_ok:
        errors.extend(list(phase_errors or []))

    if errors:
        return False, payload, errors
    return True, payload, []


def build_correction_message(errors: list[str], *, phase: str = "DEFINE") -> str:
    resolved_phase = str(phase or "").strip().upper() or "DEFINE"
    template = empty_payload(resolved_phase)
    template_json = json.dumps(template, ensure_ascii=True, indent=2)
    lines = [
        "Return ONLY a single JSON object.",
        "No markdown. No commentary.",
        "Use the canonical schema keys exactly.",
        f"Set meta.phase to: {resolved_phase}",
        "Use this JSON shape exactly (fill values, keep keys):",
        template_json,
    ]
    for idx, err in enumerate(errors or [], start=1):
        lines.append(f"{idx}. {str(err)}")
    return "\n".join(lines)


def _validate_legacy_derax_payload(payload: dict) -> tuple[bool, str]:
    required_top = {"phase", "headline", "core", "parked", "footnotes", "next", "meta"}
    if set(payload.keys()) != required_top:
        return False, "Top-level keys must match legacy DERAX schema."
    phase = str(payload.get("phase") or "").strip().upper()
    if not phase:
        return False, "Invalid phase."
    core = payload.get("core")
    if not isinstance(core, dict):
        return False, "core keys invalid."
    core_keys = {
        "end_in_mind",
        "destination_conditions",
        "non_goals",
        "adjacent_angles",
        "assumptions",
        "ambiguities",
        "risks",
        "scope_changes",
    }
    if set(core.keys()) != core_keys:
        return False, "core keys invalid."
    if not isinstance(core.get("end_in_mind"), str):
        return False, "core.end_in_mind must be string."
    for key in sorted(core_keys - {"end_in_mind"}):
        if not isinstance(core.get(key), list):
            return False, f"core.{key} must be list."
    next_block = payload.get("next")
    if not isinstance(next_block, dict) or set(next_block.keys()) != {"recommended_phase", "one_question"}:
        return False, "next keys invalid."
    meta = payload.get("meta")
    if not isinstance(meta, dict) or set(meta.keys()) != {"work_item_id", "project_id", "chat_id", "created_at"}:
        return False, "meta keys invalid."
    return True, ""


# Backward-compatible wrapper for existing DERAX call paths.
def validate_derax_response(text: str) -> tuple[bool, dict | str]:
    ok, payload, errors = validate_derax_text(text)
    if ok:
        return True, payload or {}
    canonical_error = "; ".join([str(e) for e in (errors or []) if str(e).strip()]).strip()
    if isinstance(payload, dict):
        resolved_phase = str(
            (payload.get("meta") or {}).get("phase")
            or payload.get("phase")
            or ""
        ).strip().upper()
        if resolved_phase == "EXECUTE":
            if canonical_error:
                return False, canonical_error
            return False, "Invalid DERAX JSON payload"
        legacy_ok, legacy_err = _validate_legacy_derax_payload(payload)
        if legacy_ok:
            return True, payload
        if canonical_error:
            return False, canonical_error
        if legacy_err:
            return False, legacy_err
    if canonical_error:
        return False, canonical_error
    return False, "Invalid DERAX JSON payload"


# Backward-compatible wrapper for existing DERAX call paths.
def derax_json_correction_prompt(error_text: str = "", phase: str = "DEFINE") -> str:
    errs: list[str] = []
    raw = str(error_text or "").strip()
    if raw:
        if ";" in raw:
            errs.extend([part.strip() for part in raw.split(";") if part.strip()])
        else:
            errs.append(raw)
    return build_correction_message(errs, phase=phase)
