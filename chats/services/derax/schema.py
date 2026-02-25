# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, TypedDict

try:
    from jsonschema import ValidationError, validate as jsonschema_validate
except Exception:  # pragma: no cover
    ValidationError = Exception
    jsonschema_validate = None

DERAX_PHASES = ("DEFINE", "EXPLORE", "ROUTE", "ASSEMBLE", "REFINE", "APPROVE", "EXECUTE", "COMPLETE")


class DeraxPayload(TypedDict):
    meta: dict[str, Any]
    canonical_summary: str
    intent: dict[str, Any]
    explore: dict[str, Any]
    parked_for_later: dict[str, Any]
    artefacts: dict[str, Any]
    validation: dict[str, Any]


def get_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "meta",
            "canonical_summary",
            "intent",
            "explore",
            "parked_for_later",
            "artefacts",
            "validation",
        ],
        "properties": {
            "meta": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tko_id", "derax_version", "phase", "timestamp", "source_chat_id", "source_turn_id"],
                "properties": {
                    "tko_id": {"type": "string"},
                    "derax_version": {"type": "string"},
                    "phase": {"type": "string"},
                    "timestamp": {"type": "string"},
                    "source_chat_id": {"type": "string"},
                    "source_turn_id": {"type": "string"},
                },
            },
            "canonical_summary": {"type": "string"},
            "intent": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "destination",
                    "success_criteria",
                    "constraints",
                    "non_goals",
                    "assumptions",
                    "open_questions",
                ],
                "properties": {
                    "destination": {"type": "string"},
                    "success_criteria": {"type": "array", "items": {"type": "string"}},
                    "constraints": {"type": "array", "items": {"type": "string"}},
                    "non_goals": {"type": "array", "items": {"type": "string"}},
                    "assumptions": {"type": "array", "items": {"type": "string"}},
                    "open_questions": {"type": "array", "items": {"type": "string"}},
                },
            },
            "explore": {
                "type": "object",
                "additionalProperties": False,
                "required": ["adjacent_ideas", "risks", "tradeoffs", "reframes"],
                "properties": {
                    "adjacent_ideas": {"type": "array", "items": {"type": "string"}},
                    "risks": {"type": "array", "items": {"type": "string"}},
                    "tradeoffs": {"type": "array", "items": {"type": "string"}},
                    "reframes": {"type": "array", "items": {"type": "string"}},
                },
            },
            "parked_for_later": {
                "type": "object",
                "additionalProperties": False,
                "required": ["items"],
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["title", "detail"],
                            "properties": {
                                "title": {"type": "string"},
                                "detail": {"type": "string"},
                            },
                        },
                    }
                },
            },
            "artefacts": {
                "type": "object",
                "additionalProperties": False,
                "required": ["proposed", "generated"],
                "properties": {
                    "proposed": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["kind", "title", "notes"],
                            "properties": {
                                "kind": {"type": "string"},
                                "title": {"type": "string"},
                                "notes": {"type": "string"},
                            },
                        },
                    },
                    "generated": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["artefact_id", "kind", "title"],
                            "properties": {
                                "artefact_id": {"type": "string"},
                                "kind": {"type": "string"},
                                "title": {"type": "string"},
                            },
                        },
                    },
                },
            },
            "validation": {
                "type": "object",
                "additionalProperties": False,
                "required": ["schema_ok", "errors"],
                "properties": {
                    "schema_ok": {"type": "string"},
                    "errors": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    }


def empty_payload(phase: str = "") -> DeraxPayload:
    return {
        "meta": {
            "tko_id": "",
            "derax_version": "",
            "phase": str(phase or ""),
            "timestamp": "",
            "source_chat_id": "",
            "source_turn_id": "",
        },
        "canonical_summary": "",
        "intent": {
            "destination": "",
            "success_criteria": [],
            "constraints": [],
            "non_goals": [],
            "assumptions": [],
            "open_questions": [],
        },
        "explore": {
            "adjacent_ideas": [],
            "risks": [],
            "tradeoffs": [],
            "reframes": [],
        },
        "parked_for_later": {
            "items": [],
        },
        "artefacts": {
            "proposed": [],
            "generated": [],
        },
        "validation": {
            "schema_ok": "",
            "errors": [],
        },
    }


def _minimal_structural_checks(payload: dict) -> list[str]:
    errs: list[str] = []
    if not isinstance(payload, dict):
        return ["payload must be an object"]
    top = ["meta", "canonical_summary", "intent", "explore", "parked_for_later", "artefacts", "validation"]
    for key in top:
        if key not in payload:
            errs.append("missing top-level key: " + key)
    if errs:
        return errs

    if not isinstance(payload.get("meta"), dict):
        errs.append("meta must be object")
    if not isinstance(payload.get("canonical_summary"), str):
        errs.append("canonical_summary must be string")
    if not isinstance(payload.get("intent"), dict):
        errs.append("intent must be object")
    if not isinstance(payload.get("explore"), dict):
        errs.append("explore must be object")
    if not isinstance(payload.get("parked_for_later"), dict):
        errs.append("parked_for_later must be object")
    if not isinstance(payload.get("artefacts"), dict):
        errs.append("artefacts must be object")
    if not isinstance(payload.get("validation"), dict):
        errs.append("validation must be object")
    if errs:
        return errs

    meta = payload.get("meta", {})
    for k in ["tko_id", "derax_version", "phase", "timestamp", "source_chat_id", "source_turn_id"]:
        if k not in meta:
            errs.append("missing meta key: " + k)
        elif not isinstance(meta.get(k), str):
            errs.append("meta." + k + " must be string")

    intent = payload.get("intent", {})
    for k in ["destination", "success_criteria", "constraints", "non_goals", "assumptions", "open_questions"]:
        if k not in intent:
            errs.append("missing intent key: " + k)
        elif k == "destination" and not isinstance(intent.get(k), str):
            errs.append("intent.destination must be string")
        elif k != "destination" and not isinstance(intent.get(k), list):
            errs.append("intent." + k + " must be list")

    explore = payload.get("explore", {})
    for k in ["adjacent_ideas", "risks", "tradeoffs", "reframes"]:
        if k not in explore:
            errs.append("missing explore key: " + k)
        elif not isinstance(explore.get(k), list):
            errs.append("explore." + k + " must be list")

    parked = payload.get("parked_for_later", {})
    if "items" not in parked:
        errs.append("missing parked_for_later.items")
    elif not isinstance(parked.get("items"), list):
        errs.append("parked_for_later.items must be list")

    artefacts = payload.get("artefacts", {})
    if "proposed" not in artefacts:
        errs.append("missing artefacts.proposed")
    elif not isinstance(artefacts.get("proposed"), list):
        errs.append("artefacts.proposed must be list")
    else:
        for idx, item in enumerate(list(artefacts.get("proposed") or [])):
            if not isinstance(item, dict):
                errs.append(f"artefacts.proposed[{idx}] must be object")
                continue
            for key in ("kind", "title", "notes"):
                if key not in item:
                    errs.append(f"artefacts.proposed[{idx}].{key} missing")
                elif not isinstance(item.get(key), str):
                    errs.append(f"artefacts.proposed[{idx}].{key} must be string")
    if "generated" not in artefacts:
        errs.append("missing artefacts.generated")
    elif not isinstance(artefacts.get("generated"), list):
        errs.append("artefacts.generated must be list")
    else:
        for idx, item in enumerate(list(artefacts.get("generated") or [])):
            if not isinstance(item, dict):
                errs.append(f"artefacts.generated[{idx}] must be object")
                continue
            for key in ("artefact_id", "kind", "title"):
                if key not in item:
                    errs.append(f"artefacts.generated[{idx}].{key} missing")
                elif not isinstance(item.get(key), str):
                    errs.append(f"artefacts.generated[{idx}].{key} must be string")

    validation = payload.get("validation", {})
    if "schema_ok" not in validation:
        errs.append("missing validation.schema_ok")
    elif not isinstance(validation.get("schema_ok"), str):
        errs.append("validation.schema_ok must be string")
    if "errors" not in validation:
        errs.append("missing validation.errors")
    elif not isinstance(validation.get("errors"), list):
        errs.append("validation.errors must be list")

    return errs


def validate_structural(payload: dict) -> tuple[bool, list[str]]:
    schema = get_schema()
    if jsonschema_validate is not None:
        try:
            jsonschema_validate(instance=payload, schema=schema)
            return True, []
        except ValidationError as exc:
            return False, [str(exc.message or str(exc))]
        except Exception as exc:  # pragma: no cover
            return False, [str(exc)]
    errs = _minimal_structural_checks(payload)
    return len(errs) == 0, errs


# Backward-compatible helper used by existing modules.
def empty_derax_payload(phase: str, meta: dict | None = None) -> dict:
    meta_in = dict(meta or {})
    resolved_phase = str(phase or "").strip().upper()
    return {
        "phase": resolved_phase,
        "headline": "",
        "core": {
            "end_in_mind": "",
            "destination_conditions": [],
            "non_goals": [],
            "adjacent_angles": [],
            "assumptions": [],
            "ambiguities": [],
            "risks": [],
            "scope_changes": [],
        },
        "parked": [],
        "footnotes": [],
        "next": {
            "recommended_phase": resolved_phase or "DEFINE",
            "one_question": "",
        },
        "meta": {
            "work_item_id": str(meta_in.get("work_item_id") or ""),
            "project_id": meta_in.get("project_id"),
            "chat_id": meta_in.get("chat_id"),
            "created_at": str(meta_in.get("created_at") or ""),
        },
    }
