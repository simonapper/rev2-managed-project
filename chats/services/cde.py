# -*- coding: utf-8 -*-
# chats/services/cde.py
#
# CDE v1 - Chat Definition Editor field validation loop helpers.
# NOTE: Keep all output JSON-only (handled by the LLM via the CDE boilerplate).
# NOTE: Keep code comments 7-bit ASCII only.

from __future__ import annotations

import json
from typing import Any, Dict, Optional


CDE_VALIDATOR_BOILERPLATE = (
    "You are validating one chat-definition field for clarity and stability.\n"
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


def _parse_output_json(raw_output: str, field_key: str) -> Dict[str, Any]:
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
    CDE v1: Validate one chat-definition field value for PASS/WEAK/CONFLICT
    and return the structured validation object (dict).

    Notes:
    - generate_panes_func is injected to avoid circular imports.
      Pass your generate_panes function.
    - The LLM must emit the CDE JSON object in panes["output"].
    """
    locked_fields = locked_fields or {}
    value_text = (value_text or "").strip()

    system_blocks = [CDE_VALIDATOR_BOILERPLATE]

    if rubric:
        rubric = (rubric or "").strip()
        if rubric:
            system_blocks.append("Field rubric (apply lightly):\n" + rubric + "\n")

    system_blocks.append(_locked_fields_block(locked_fields))

    llm_input = "Field key: " + field_key + "\n" + "Field value:\n" + value_text

    panes = generate_panes_func(
        llm_input,
        image_parts=None,
        system_blocks=system_blocks,
    )


    out = _parse_output_json(
        raw_output=str(panes.get("output") or ""),
        field_key=field_key,
    )

    # Debug payload for "Show system" in UI.
    # Keep keys private-ish (underscore) so they do not collide with schema.
    out["debug_system_blocks"] = system_blocks
    out["debug_user_text"] = llm_input

    return out


def format_chat_goal_system_block(
    *,
    locked_chat_fields: Dict[str, str],
    max_constraints: int = 3,
    max_non_goals: int = 3,
) -> str:
    """
    Produce a compact SYSTEM block used on every assistant-generation call for managed chats.
    Keep it short: goal + optional success + constraints + non-goals.

    Expected keys in locked_chat_fields (suggested):
    - chat.goal
    - chat.success
    - chat.constraints (may be multi-line or semicolon separated)
    - chat.non_goals (may be multi-line or semicolon separated)
    """
    def _norm(s: str) -> str:
        return (s or "").strip()

    def _split_lines(s: str, max_items: int) -> list[str]:
        s = _norm(s)
        if not s:
            return []
        # Accept either newlines or semicolons as separators.
        parts = []
        for raw in s.replace(";", "\n").splitlines():
            t = raw.strip(" \t-")
            if t:
                parts.append(t)
        return parts[:max_items]

    goal = _norm(locked_chat_fields.get("chat.goal", ""))
    success = _norm(locked_chat_fields.get("chat.success", ""))

    constraints_items = _split_lines(locked_chat_fields.get("chat.constraints", ""), max_constraints)
    non_goal_items = _split_lines(locked_chat_fields.get("chat.non_goals", ""), max_non_goals)

    lines: list[str] = []
    lines.append("Managed Chat Direction:")
    if goal:
        lines.append("- Chat goal: " + goal)
    else:
        lines.append("- Chat goal: (not set)")

    if success:
        lines.append("- Success: " + success)

    if constraints_items:
        lines.append("- Constraints:")
        for c in constraints_items:
            lines.append("  - " + c)

    if non_goal_items:
        lines.append("- Non-goals:")
        for ng in non_goal_items:
            lines.append("  - " + ng)

    return "\n".join(lines) + "\n"

# Version 2 that uses the LLM to explore what the user doesn't know about
# the chat and where it should go.

CDE_DRAFT_BOILERPLATE = (
    "You turn free-form user intent into a draft chat definition.\n"
    "Return JSON only, matching this schema:\n"
    "{\n"
    '  "hypotheses": {\n'
    '    "goal": "string",\n'
    '    "success": "string",\n'
    '    "constraints": ["string"],\n'
    '    "non_goals": ["string"],\n'
    '    "assumptions": ["string"],\n'
    '    "open_questions": ["string"]\n'
    "  }\n"
    "}\n"
    "Rules:\n"
    "- Keep goal/success to one sentence each.\n"
    "- constraints/non_goals max 3 each.\n"
    "- assumptions max 5.\n"
    "- open_questions max 3, only if needed.\n"
    "- Do not invent facts; reflect uncertainty.\n"
)

def draft_cde_from_seed(*, generate_panes_func, seed_text: str) -> Dict[str, Any]:
    seed_text = (seed_text or "").strip()
    system_blocks = [CDE_DRAFT_BOILERPLATE]
    panes = generate_panes_func(
        "Seed intent:\n" + seed_text,
        image_parts=None,
        system_blocks=system_blocks,
    )
    raw = (panes.get("output") or "").strip()
    try:
        data = json.loads(raw)
    except Exception:
        return {"ok": False, "error": "Draft OUTPUT was not valid JSON.", "raw": raw}
    hyp = data.get("hypotheses") if isinstance(data, dict) else None
    if not isinstance(hyp, dict):
        return {"ok": False, "error": "Draft JSON missing hypotheses.", "raw": raw}
    return {"ok": True, "draft": {"hypotheses": hyp}, "raw": raw}
