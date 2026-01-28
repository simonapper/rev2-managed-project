# -*- coding: utf-8 -*-
# projects/services/pde.py
#
# PDE v1 - L1 field validation loop helpers.
# NOTE: Keep all output JSON-only (handled by the LLM via the PDE boilerplate).

from __future__ import annotations

import json
from typing import Any, Dict, Optional


PDE_VALIDATOR_BOILERPLATE = (
    "You are validating one project-definition field for clarity and stability.\n"
    "\n"
    "Classify the field value as exactly one:\n"
    "- PASS: clear, unambiguous, stable enough to lock.\n"
    "- WEAK: vague or underspecified; needs refinement.\n"
    "- CONFLICT: contradicts another locked field provided in context.\n"
    "\n"
    "Return OUTPUT as valid JSON only, matching this schema:\n"
    "{\n"
    '  "field_key": "string",\n'
    '  "verdict": "PASS | WEAK | CONFLICT",\n'
    '  "issues": ["string"],\n'
    '  "suggested_revision": "string",\n'
    '  "questions": ["string"],\n'
    '  "confidence": "LOW | MEDIUM | HIGH"\n'
    "}\n"
    "\n"
    "Rules:\n"
    "- No prose outside JSON in OUTPUT.\n"
    "- issues: empty if PASS.\n"
    "- questions: max 3, only if needed.\n"
    "- suggested_revision: provide a best improved rewrite even if WEAK/CONFLICT.\n"
)


def _locked_fields_block(locked_fields: Dict[str, str]) -> str:
    """
    Provide context for CONFLICT detection.
    Keep it simple and explicit; this is SYSTEM content.
    """
    if not locked_fields:
        return "Locked fields context: (none)\n"

    # Stable ordering for determinism.
    keys = sorted(locked_fields.keys())
    lines = ["Locked fields context:"]
    for k in keys:
        v = (locked_fields.get(k) or "").strip()
        if not v:
            continue
        # Avoid accidental JSON or markdown; plain text only.
        lines.append("- " + k + ": " + v)
    if len(lines) == 1:
        lines.append("(none)")
    return "\n".join(lines) + "\n"


def _parse_output_json(
    raw_output: str,
    field_key: str,
) -> Dict[str, Any]:
    """
    Parse the LLM OUTPUT pane as JSON and do minimal sanity checks.
    If anything is wrong, return a WEAK verdict with a safe suggested revision.
    """
    raw_output = (raw_output or "").strip()

    try:
        data = json.loads(raw_output)
    except Exception:
        return {
            "field_key": field_key,
            "verdict": "WEAK",
            "issues": ["OUTPUT was not valid JSON."],
            "suggested_revision": ("" if not raw_output else raw_output),
            "questions": ["Re-run verify; if persists, check prompt/contract."],
            "confidence": "LOW",
        }

    if not isinstance(data, dict):
        return {
            "field_key": field_key,
            "verdict": "WEAK",
            "issues": ["OUTPUT JSON was not an object."],
            "suggested_revision": "",
            "questions": ["Re-run verify; if persists, check prompt/contract."],
            "confidence": "LOW",
        }

    # Minimal normalisation + required keys.
    out: Dict[str, Any] = {}
    out["field_key"] = str(data.get("field_key") or field_key)

    verdict = str(data.get("verdict") or "").strip().upper()
    if verdict not in ("PASS", "WEAK", "CONFLICT"):
        verdict = "WEAK"
    out["verdict"] = verdict

    issues = data.get("issues")
    if isinstance(issues, list):
        out["issues"] = [str(x) for x in issues if str(x).strip()]
    else:
        out["issues"] = []

    out["suggested_revision"] = str(data.get("suggested_revision") or "")

    questions = data.get("questions")
    if isinstance(questions, list):
        qs = [str(x) for x in questions if str(x).strip()]
        out["questions"] = qs[:3]
    else:
        out["questions"] = []

    confidence = str(data.get("confidence") or "").strip().upper()
    if confidence not in ("LOW", "MEDIUM", "HIGH"):
        confidence = "LOW"
    out["confidence"] = confidence

    # Enforce rule: issues empty if PASS.
    if out["verdict"] == "PASS":
        out["issues"] = []

    return out


def validate_field(
    *,
    generate_panes_func,
    field_key: str,
    value_text: str,
    locked_fields: Optional[Dict[str, str]] = None,
    rubric: Optional[str] = None,
) -> Dict[str, Any]:
    """
    PDE v1: Validate one field value for PASS/WEAK/CONFLICT and return
    the structured validation object (dict).

    Notes:
    - generate_panes_func is injected to avoid circular imports.
      Pass your generate_panes function.
    - The LLM must emit the PDE JSON object in panes["output"].
    """
    locked_fields = locked_fields or {}
    value_text = (value_text or "").strip()

    system_blocks = [PDE_VALIDATOR_BOILERPLATE]

    if rubric:
        rubric = (rubric or "").strip()
        if rubric:
            system_blocks.append("Field rubric (apply lightly):\n" + rubric + "\n")

    system_blocks.append(_locked_fields_block(locked_fields))

    # Keep user_text minimal. We want the model to focus on the field value.
    # Include field_key so it can echo it in OUTPUT.
    llm_input = (
        "Field key: " + field_key + "\n"
        "Field value:\n"
        + value_text
    )

    panes = generate_panes_func(
        llm_input,
        image_parts=None,
        system_blocks=system_blocks,
    )

    return _parse_output_json(
        raw_output=str(panes.get("output") or ""),
        field_key=field_key,
    )
