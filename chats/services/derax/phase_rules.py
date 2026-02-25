# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any

from chats.services.derax.contracts import get_phase_manifest

_MISSING = object()


def required_paths_for_phase(phase: str) -> list[str]:
    return list(get_phase_manifest(phase)["required_paths"])


def _get_by_dotted_path(payload: dict, path: str):
    current: Any = payload
    for part in str(path or "").split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current.get(part)
    return current


def _is_nonempty(value: Any) -> bool:
    if value is _MISSING:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        if len(value) == 0:
            return False
        if all(isinstance(item, str) for item in value):
            return any(str(item).strip() != "" for item in value)
        if all(isinstance(item, dict) for item in value):
            for item in value:
                if any(_is_nonempty(v) for v in item.values()):
                    return True
            return False
        return True
    if isinstance(value, dict):
        if len(value) == 0:
            return False
        return any(_is_nonempty(v) for v in value.values())
    return False


def check_required_nonempty(payload: dict, phase: str | None = None) -> tuple[bool, list[str]]:
    if not isinstance(payload, dict):
        return False, ["Missing or empty: payload"]

    resolved_phase = str(phase or "").strip().upper()
    if not resolved_phase:
        meta = payload.get("meta", {})
        if isinstance(meta, dict):
            resolved_phase = str(meta.get("phase") or "").strip().upper()
    if not resolved_phase:
        return False, ["Missing or empty: meta.phase"]

    try:
        required_paths = required_paths_for_phase(resolved_phase)
    except ValueError:
        return False, [f"Unsupported DERAX phase: {resolved_phase}"]

    errors: list[str] = []
    for path in required_paths:
        value = _get_by_dotted_path(payload, path)
        if not _is_nonempty(value):
            errors.append(f"Missing or empty: {path}")
    return len(errors) == 0, errors
