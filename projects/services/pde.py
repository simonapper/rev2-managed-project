# -*- coding: utf-8 -*-
# projects/services/pde.py
#
# PDE v1 - Field validation and draft helpers (LLM only).
# NOTE: Keep all output JSON-only (handled by the LLM via the PDE boilerplate).
# NOTE: Keep code comments 7-bit ASCII only.

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

PDE_DRAFT_BOILERPLATE = (
    "You turn free-form user intent into a draft Project CKO field set.\n"
    "Return JSON only, matching this schema:\n"
    "{\n"
    '  "hypotheses": {\n'
    '    "fields": {\n'
    '      "canonical.summary": "string",\n'
    '      "identity.project_type": "string",\n'
    '      "identity.project_status": "string",\n'
    '      "intent.primary_goal": "string",\n'
    '      "intent.success_criteria": "string",\n'
    '      "scope.in_scope": "string",\n'
    '      "scope.out_of_scope": "string",\n'
    '      "scope.hard_constraints": "string",\n'
    '      "authority.primary": "string",\n'
    '      "authority.secondary": "string",\n'
    '      "authority.deviation_rules": "string",\n'
    '      "posture.interpretive_rules": "string",\n'
    '      "posture.epistemic_constraints": "string",\n'
    '      "posture.novelty_rules": "string",\n'
    '      "storage.artefact_root_ref": "string",\n'
    '      "storage.canonical_artefact_types": "string",\n'
    '      "storage.non_authoritative": "string",\n'
    '      "context.narrative": "string"\n'
    "    }\n"
    "  }\n"
    "}\n"
    "\n"
    "Rules:\n"
    "- Keep canonical.summary to <=10 words.\n"
    "- Use enum values where possible:\n"
    "  - identity.project_type: META|KNOWLEDGE|DELIVERY|RESEARCH|OPERATIONS\n"
    "  - identity.project_status: ACTIVE|PAUSED|ARCHIVED\n"
    "- scope fields may be bullet lists in a single string.\n"
    "- Do not invent facts; reflect uncertainty.\n"
    "- If unknown, use an explicit placeholder like 'DEFERRED'.\n"
)


def _locked_fields_block(locked_fields: Dict[str, str]) -> str:
    if not locked_fields:
        return "Locked fields context: (none)\n"
    keys = sorted(locked_fields.keys())
    lines = ["Locked fields context:"]
    for k in keys:
        v = (locked_fields.get(k) or "").strip()
        if not v:
            continue
        lines.append("- " + k + ": " + v)
    if len(lines) == 1:
        lines.append("(none)")
    return "\n".join(lines) + "\n"


def _parse_output_json(raw_output: str, field_key: str) -> Dict[str, Any]:
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
    locked_fields = locked_fields or {}
    value_text = (value_text or "").strip()

    system_blocks = [PDE_VALIDATOR_BOILERPLATE]

    rr = (rubric or "").strip()
    if rr:
        system_blocks.append("Field rubric (apply lightly):\n" + rr + "\n")

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

    out["debug_system_blocks"] = system_blocks
    out["debug_user_text"] = llm_input
    return out


def draft_pde_from_seed(*, generate_panes_func, seed_text: str) -> Dict[str, Any]:
    seed_text = (seed_text or "").strip()
    system_blocks = [PDE_DRAFT_BOILERPLATE]
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

    fields = hyp.get("fields")
    if not isinstance(fields, dict):
        return {"ok": False, "error": "Draft JSON missing hypotheses.fields.", "raw": raw}

    out_fields: Dict[str, str] = {}
    for k, v in fields.items():
        kk = str(k).strip()
        if not kk:
            continue
        out_fields[kk] = ("" if v is None else str(v)).strip()

    return {"ok": True, "draft": {"hypotheses": {"fields": out_fields}}, "raw": raw}
