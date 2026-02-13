from __future__ import annotations

import json
from html import escape
from typing import Any, Dict, Iterable, List, Tuple

SECTION_MAP = {
    "CKO": [
        ("canonical_summary", "Canonical summary"),
        ("scope", "Scope"),
        ("statement", "Statement"),
        ("supporting_basis", "Supporting basis"),
        ("assumptions", "Assumptions"),
        ("alternatives_considered", "Alternatives considered"),
        ("uncertainties_limits", "Uncertainties / limits"),
        ("provenance", "Provenance"),
    ],
    "WKO": [
        ("purpose", "Purpose"),
        ("current_state", "Current state"),
        ("open_questions", "Open questions"),
        ("options_candidate_approaches", "Options / candidate approaches"),
        ("risks_dependencies", "Risks / dependencies"),
        ("next_actions", "Next actions"),
        ("provenance", "Provenance"),
    ],
    "TKO": [
        ("canonical_summary", "Canonical summary"),
        ("working_preferences", "Working preferences"),
        ("context", "Context / why this exists"),
        ("current_state", "Current state"),
        ("decisions_made", "Decisions made (and why)"),
        ("in_scope_next", "In scope next"),
        ("out_of_scope", "Out of scope"),
        ("known_risks", "Known risks / gotchas"),
        ("files_modules_commands", "Files / modules / commands"),
        ("next_step", "Next step (single, concrete)"),
    ],
    "PKO": [
        ("policy_summary", "Policy summary"),
        ("policy_statement", "Policy statement"),
        ("rationale", "Rationale"),
        ("applies_to", "Applies to"),
        ("does_not_apply_to", "Does not apply to"),
        ("enforcement", "Enforcement"),
        ("exceptions", "Exceptions"),
        ("versioning_provenance", "Versioning / provenance"),
    ],
}

CKO_KEY_MAP = {
    "canonical.summary": "canonical_summary",
    "scope.in_scope": "scope",
    "scope.out_of_scope": "scope",
    "scope.hard_constraints": "scope",
    "intent.primary_goal": "statement",
    "intent.success_criteria": "supporting_basis",
    "authority.primary": "supporting_basis",
    "authority.secondary": "supporting_basis",
    "authority.deviation_rules": "supporting_basis",
    "posture.epistemic_constraints": "assumptions",
    "posture.novelty_rules": "assumptions",
    "context.narrative": "supporting_basis",
}


def _normalise_key(raw: str) -> str:
    key = (raw or "").strip().lower()
    return key.replace(" ", "_").replace("-", "_").replace("/", "_")


def _stringify_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            text = _stringify_value(item)
            if text:
                parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        return json.dumps(value, indent=2, ensure_ascii=True)
    return str(value).strip()


def _render_sections(payload: Dict[str, Any], order: Iterable[Tuple[str, str]]) -> str:
    blocks: List[str] = []
    for key, label in order:
        raw = payload.get(key)
        text = _stringify_value(raw)
        if not text:
            continue
        body_text = escape(text).rstrip() + "\n"
        blocks.append(
            "<div class=\"rw-artefact-section\" style=\"margin-bottom:0.75rem;\">"
            "<div class=\"fw-semibold\">"
            + escape(label)
            + "</div>"
            "<div style=\"white-space:pre-wrap;margin-top:0.25rem;\">"
            + body_text
            + "</div>"
            "</div>"
        )
    return "\n".join(blocks)


def _append_cko_fields(payload: Dict[str, Any], locked_fields: Dict[str, Any]) -> Dict[str, Any]:
    for raw_key, mapped in CKO_KEY_MAP.items():
        value = _stringify_value(locked_fields.get(raw_key))
        if not value:
            continue
        existing = _stringify_value(payload.get(mapped))
        if existing:
            payload[mapped] = existing + "\n" + value
        else:
            payload[mapped] = value
    return payload


def render_artefact_html(kind: str, payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    kind_key = (kind or "").strip().upper()
    if kind_key == "PDO":
        return _render_pdo(payload)
    order = SECTION_MAP.get(kind_key)
    if order:
        return _render_sections(payload, order)
    items = []
    for key, value in payload.items():
        label = key.replace("_", " ").strip().title() or "Field"
        text = _stringify_value(value)
        if not text:
            continue
        body_text = escape(text).rstrip() + "\n"
        items.append(
            "<div class=\"rw-artefact-section\" style=\"margin-bottom:0.75rem;\">"
            "<div class=\"fw-semibold\">"
            + escape(label)
            + "</div>"
            "<div style=\"white-space:pre-wrap;margin-top:0.25rem;\">"
            + body_text
            + "</div>"
            "</div>"
        )
    return "\n".join(items)


def _render_pdo(payload: Dict[str, Any]) -> str:
    blocks: List[str] = []
    summary = _stringify_value(payload.get("pdo_summary"))
    if summary:
        blocks.append(_render_sections({"pdo_summary": summary}, [("pdo_summary", "PDO summary")]))

    alignment = payload.get("cko_alignment") if isinstance(payload.get("cko_alignment"), dict) else {}
    align_payload = {
        "stage1_inputs_match": _stringify_value(alignment.get("stage1_inputs_match")),
        "final_outputs_match": _stringify_value(alignment.get("final_outputs_match")),
    }
    if align_payload.get("stage1_inputs_match") or align_payload.get("final_outputs_match"):
        blocks.append(
            _render_sections(
                align_payload,
                [
                    ("stage1_inputs_match", "CKO alignment: stage 1 inputs match"),
                    ("final_outputs_match", "CKO alignment: final outputs match"),
                ],
            )
        )

    core_payload = {
        "planning_purpose": _stringify_value(payload.get("planning_purpose")),
        "planning_constraints": _stringify_value(payload.get("planning_constraints")),
        "assumptions": _stringify_value(payload.get("assumptions")),
    }
    if core_payload.get("planning_purpose") or core_payload.get("planning_constraints") or core_payload.get("assumptions"):
        blocks.append(
            _render_sections(
                core_payload,
                [
                    ("planning_purpose", "Planning purpose"),
                    ("planning_constraints", "Planning constraints"),
                    ("assumptions", "Assumptions"),
                ],
            )
        )

    stages = payload.get("stages") if isinstance(payload.get("stages"), list) else []
    if stages:
        stage_blocks: List[str] = []
        for item in stages:
            if not isinstance(item, dict):
                continue
            stage_number = item.get("stage_number")
            title = _stringify_value(item.get("title"))
            heading = "Stage"
            if stage_number:
                heading = f"Stage {stage_number}"
            if title:
                heading = f"{heading}: {title}"
            stage_body = _render_sections(
                {
                    "status": _stringify_value(item.get("status")),
                    "purpose": _stringify_value(item.get("purpose")),
                    "inputs": _stringify_value(item.get("inputs")),
                    "stage_process": _stringify_value(item.get("stage_process")),
                    "outputs": _stringify_value(item.get("outputs")),
                    "assumptions": _stringify_value(item.get("assumptions")),
                    "duration_estimate": _stringify_value(item.get("duration_estimate")),
                    "risks_notes": _stringify_value(item.get("risks_notes")),
                },
                [
                    ("status", "Status"),
                    ("purpose", "Purpose"),
                    ("inputs", "Inputs"),
                    ("stage_process", "Stage process"),
                    ("outputs", "Outputs"),
                    ("assumptions", "Assumptions"),
                    ("duration_estimate", "Duration estimate"),
                    ("risks_notes", "Risks / notes"),
                ],
            )
            stage_blocks.append(
                "<div class=\"rw-artefact-section\" style=\"margin-bottom:1rem;\">"
                "<div class=\"fw-semibold\">"
                + escape(heading)
                + "</div>"
                "<div style=\"margin-top:0.35rem;\">"
                + stage_body
                + "</div>"
                "</div>"
            )
        if stage_blocks:
            blocks.append("\n".join(stage_blocks))
    return "\n".join(blocks)


def build_cko_payload(locked_fields: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    return _append_cko_fields(payload, locked_fields)
