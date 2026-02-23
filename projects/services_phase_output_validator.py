# -*- coding: utf-8 -*-

from __future__ import annotations

import re
from typing import Any

from projects.phase_contracts import PHASE_CONTRACTS


_LABEL_ENFORCED_PHASES = {"DEFINE", "EXPLORE", "REFINE", "APPROVE"}


def _required_headers_for_phase(active_phase: str) -> list[str]:
    phase = str(active_phase or "").strip().upper()
    contract = PHASE_CONTRACTS.get(phase) or {}
    headers = contract.get("output_requirements") or []
    return [str(h).strip() for h in headers if str(h).strip()]


def find_missing_required_headers(*, work_item: Any, text: str) -> list[str]:
    if not work_item:
        return []
    required = _required_headers_for_phase(getattr(work_item, "active_phase", ""))
    if not required:
        return []

    body = str(text or "")
    missing: list[str] = []
    for header in required:
        pattern = r"(?mi)^\s*" + re.escape(header) + r"\s*$"
        if re.search(pattern, body) is None:
            missing.append(header)
    return missing


def _find_missing_boundary_labels(*, work_item: Any, text: str) -> list[str]:
    if not work_item:
        return []
    phase = str(getattr(work_item, "active_phase", "") or "").strip().upper()
    if phase not in _LABEL_ENFORCED_PHASES:
        return []

    profile = dict(getattr(work_item, "boundary_profile_json", {}) or {})
    if not profile:
        return []

    required = list(profile.get("required_labels") or [])
    if not required:
        required = ["Scope", "Assumptions", "Source basis", "Confidence"]

    body = str(text or "")
    missing: list[str] = []
    for label in required:
        pattern = r"(?mi)^\s*" + re.escape(str(label)) + r"\s*:\s*.+$"
        if re.search(pattern, body) is None:
            missing.append(str(label))
    return missing


def validate_phase_output(*, work_item: Any, text: str) -> tuple[bool, list[str]]:
    missing = find_missing_required_headers(work_item=work_item, text=text)
    missing_labels = _find_missing_boundary_labels(work_item=work_item, text=text)
    for label in missing_labels:
        marker = f"{label}:"
        if marker not in missing:
            missing.append(marker)
    return (len(missing) == 0, missing)


def build_phase_correction_request(*, missing_headers: list[str], draft_text: str) -> str:
    required = ", ".join([str(h) for h in (missing_headers or []) if str(h).strip()])
    lines = [
        "Rewrite with the missing sections: " + required,
        "Use the required section headers exactly as listed.",
        "",
        "Draft to revise:",
        str(draft_text or ""),
    ]
    return "\n".join(lines).strip()
