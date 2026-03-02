# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
from typing import Any

from chats.services.derax.contracts import get_phase_manifest
from chats.services.derax.phase_rules import check_required_nonempty
from chats.services.derax.schema import empty_payload, validate_structural


def _stringify(value: Any) -> str:
    return str(value or "").strip()


def _listify(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = _stringify(value)
    return [text] if text else []


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        try:
            return {k: v for k, v in value.items()}
        except Exception:
            return {}
    return {}


def _mapping_items(value: Any) -> list[tuple[Any, Any]]:
    if isinstance(value, dict):
        try:
            return list(value.items())
        except Exception:
            return []
    if isinstance(value, list):
        items: list[tuple[Any, Any]] = []
        for row in value:
            if isinstance(row, (list, tuple)) and len(row) == 2:
                items.append((row[0], row[1]))
        return items
    return []


def _get_by_dotted_path(payload: dict, path: str):
    current: Any = payload
    for part in str(path or "").split("."):
        if not part:
            continue
        if not isinstance(current, dict) or part not in current:
            return None
        current = current.get(part)
    return current


def _is_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        if len(value) == 0:
            return False
        if all(isinstance(item, str) for item in value):
            return any(str(item).strip() for item in value)
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
    return True


def _word_count(text: str) -> int:
    raw = _stringify(text)
    if not raw:
        return 0
    return len([tok for tok in re.split(r"\s+", raw) if tok.strip()])


def _count_items(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return 1 if _is_nonempty(value) else 0
    return len(_listify(value))


def _forbidden_prefix_has_content(payload: dict, prefix: str) -> bool:
    target = str(prefix or "").strip()
    if target.endswith("."):
        target = target[:-1]
    if not target:
        return False
    value = _get_by_dotted_path(payload, target)
    return _is_nonempty(value)


def _execute_row_has_placeholder_text(row: dict) -> bool:
    text = " ".join(
        [
            _stringify(row.get("kind")),
            _stringify(row.get("title")),
            _stringify(row.get("notes")),
        ]
    ).lower()
    if not text.strip():
        return True
    markers = ("tbd", "todo", "placeholder", "unknown", "n/a", "to be decided", "to be confirmed", "???")
    return any(marker in text for marker in markers)


def _execute_generated_row_has_empty_fields(row: dict) -> bool:
    return not (
        _stringify(row.get("artefact_id"))
        and _stringify(row.get("kind"))
        and _stringify(row.get("title"))
    )


def _phase_policy_errors(payload: dict) -> list[str]:
    errors: list[str] = []
    meta = _as_dict(payload.get("meta"))
    phase = _stringify(meta.get("phase")).upper()
    if not phase:
        return errors
    try:
        manifest = get_phase_manifest(phase)
    except Exception:
        return errors

    if phase == "EXECUTE":
        artefacts = _as_dict(payload.get("artefacts"))
        proposed = list(artefacts.get("proposed") or [])
        generated = list(artefacts.get("generated") or [])
        requirements = _as_dict(artefacts.get("requirements"))

        # If generated is present, rows must be complete (no empty sections).
        for idx, row in enumerate(generated):
            if not isinstance(row, dict):
                errors.append(f"EXECUTE generated row invalid at index {idx}: must be object")
                continue
            if _execute_generated_row_has_empty_fields(row):
                errors.append(
                    f"EXECUTE generated row {idx} has empty fields. artefact_id/kind/title must be non-empty."
                )

        # Infer missing-input state from placeholders/empty proposal rows.
        inferred_insufficient = False
        if not proposed and not generated:
            inferred_insufficient = True
        for row in proposed:
            if not isinstance(row, dict):
                inferred_insufficient = True
                continue
            if _execute_row_has_placeholder_text(row):
                inferred_insufficient = True

        if inferred_insufficient and not _is_nonempty(requirements):
            errors.append(
                "EXECUTE sufficiency check failed: add artefacts.requirements when inputs are missing."
            )
        if _is_nonempty(requirements) and generated:
            errors.append(
                "EXECUTE should not populate artefacts.generated when artefacts.requirements is present."
            )
        return errors

    if phase != "DEFINE":
        return errors

    for pref in list(manifest.get("forbidden_prefixes") or []):
        if _forbidden_prefix_has_content(payload, str(pref)):
            errors.append(f"Forbidden content present under {pref} for phase {phase}")

    define_caps = {
        "intent.open_questions": 3,
        "parked_for_later.items": 3,
        "intent.assumptions": 1,
        "intent.success_criteria": 0,
        "artefacts.proposed": 0,
        "canonical_summary_words": 10,
    }
    manifest_caps = manifest.get("caps")
    if isinstance(manifest_caps, dict):
        for key, value in manifest_caps.items():
            try:
                define_caps[str(key)] = int(value)
            except Exception:
                continue

    # Explicitly forbidden for DEFINE even if not listed as forbidden prefixes.
    for hard_forbidden_path in ("intent.success_criteria", "artefacts.proposed"):
        value = _get_by_dotted_path(payload, hard_forbidden_path)
        if _is_nonempty(value):
            errors.append(f"Forbidden content present under {hard_forbidden_path} for phase DEFINE")

    for path in (
        "intent.open_questions",
        "parked_for_later.items",
        "intent.assumptions",
        "intent.success_criteria",
        "artefacts.proposed",
    ):
        cap = int(define_caps.get(path, 0))
        value = _get_by_dotted_path(payload, path)
        count = _count_items(value)
        if count > cap:
            errors.append(f"Cap exceeded: {path} has {count} items (max {cap}) for phase DEFINE")

    max_words = int(define_caps.get("canonical_summary_words", 10))
    canonical_summary = _stringify(payload.get("canonical_summary"))
    words = _word_count(canonical_summary)
    if words > max_words:
        errors.append(
            f"Cap exceeded: canonical_summary has {words} words (max {max_words}) for phase DEFINE"
        )

    return errors


def _dict_from_maybe_json(value: Any) -> dict | None:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        text = str(value or "").strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
            except Exception:
                return None
            if isinstance(parsed, dict):
                return dict(parsed)
    return None


def _normalise_payload_shape(payload: dict) -> dict:
    out = _as_dict(payload)
    canonical_keys = {
        "meta",
        "canonical_summary",
        "intent",
        "explore",
        "parked_for_later",
        "artefacts",
        "validation",
    }
    partial_markers = {"meta", "intent", "explore", "parked_for_later", "artefacts", "validation"}
    root_alias_keys = {
        "destination",
        "success_criteria",
        "constraints",
        "non_goals",
        "assumptions",
        "open_questions",
        "adjacent_ideas",
        "risks",
        "tradeoffs",
        "reframes",
    }
    root_meta_alias_keys = {
        "derax_version",
        "tko_id",
        "timestamp",
        "source_chat_id",
        "source_turn_id",
    }

    def _apply_local_shape_rules(obj: dict) -> dict:
        if canonical_keys.issubset(set(obj.keys())):
            return obj

        # Partial canonical payload: pad missing top-level keys from empty template.
        if len(set(obj.keys()).intersection(partial_markers)) >= 2:
            phase_guess = _stringify((_as_dict(obj.get("meta"))).get("phase")).upper()
            if not phase_guess:
                phase_guess = _stringify(obj.get("phase")).upper() or "DEFINE"
            seeded = empty_payload(phase_guess)
            for key in canonical_keys:
                if key in obj:
                    seeded[key] = obj.get(key)
            seeded_meta = _as_dict(seeded.get("meta"))
            if not _stringify(seeded_meta.get("derax_version")):
                seeded_meta["derax_version"] = _stringify(obj.get("derax_version"))
            if not _stringify(seeded_meta.get("tko_id")):
                seeded_meta["tko_id"] = _stringify(obj.get("tko_id"))
            if not _stringify(seeded_meta.get("timestamp")):
                seeded_meta["timestamp"] = _stringify(obj.get("timestamp"))
            if not _stringify(seeded_meta.get("source_chat_id")):
                seeded_meta["source_chat_id"] = _stringify(obj.get("source_chat_id"))
            if not _stringify(seeded_meta.get("source_turn_id")):
                seeded_meta["source_turn_id"] = _stringify(obj.get("source_turn_id"))
            seeded["meta"] = seeded_meta
            return seeded

        # Root-alias payload: phase + flat fields without canonical envelope.
        if _stringify(obj.get("phase")) and len(set(obj.keys()).intersection(root_alias_keys)) >= 2:
            phase_guess = _stringify(obj.get("phase")).upper() or "DEFINE"
            seeded = empty_payload(phase_guess)
            for key in canonical_keys:
                if key in obj:
                    seeded[key] = obj.get(key)
            for key in root_alias_keys:
                if key in obj:
                    seeded[key] = obj.get(key)
            return seeded
        return obj

    out = _apply_local_shape_rules(out)
    if canonical_keys.issubset(set(out.keys())):
        return out

    # Root meta-alias payload: top-level phase/meta fragments.
    if _stringify(out.get("phase")) and len(set(out.keys()).intersection(root_meta_alias_keys)) >= 2:
        phase_guess = _stringify(out.get("phase")).upper() or "DEFINE"
        seeded = empty_payload(phase_guess)
        for key in canonical_keys:
            if key in out:
                seeded[key] = out.get(key)
        seeded_meta = _as_dict(seeded.get("meta"))
        seeded_meta["phase"] = phase_guess
        if not _stringify(seeded_meta.get("derax_version")):
            seeded_meta["derax_version"] = _stringify(out.get("derax_version"))
        if not _stringify(seeded_meta.get("tko_id")):
            seeded_meta["tko_id"] = _stringify(out.get("tko_id"))
        if not _stringify(seeded_meta.get("timestamp")):
            seeded_meta["timestamp"] = _stringify(out.get("timestamp"))
        if not _stringify(seeded_meta.get("source_chat_id")):
            seeded_meta["source_chat_id"] = _stringify(out.get("source_chat_id"))
        if not _stringify(seeded_meta.get("source_turn_id")):
            seeded_meta["source_turn_id"] = _stringify(out.get("source_turn_id"))
        seeded["meta"] = seeded_meta
        out = seeded

    # Unwrap common wrappers first.
    for key in ("payload", "data", "response", "result", "output", "json"):
        inner = _dict_from_maybe_json(out.get(key))
        if inner is not None:
            out = inner
            break

    out = _apply_local_shape_rules(out)
    if canonical_keys.issubset(set(out.keys())):
        return out

    # Convert legacy payload format to canonical for any phase.
    # Accept full legacy payload and partial-legacy payloads that still carry "core".
    legacy_keys = {"phase", "headline", "core", "parked", "footnotes", "next", "meta"}
    has_legacy_core = isinstance(out.get("core"), dict)
    has_legacy_shape = legacy_keys.issubset(set(out.keys())) or has_legacy_core
    if has_legacy_shape:
        phase = _stringify(out.get("phase")).upper() or "DEFINE"
        core = _as_dict(out.get("core"))
        legacy_meta = _as_dict(out.get("meta"))
        parked = list(out.get("parked") or out.get("parked_for_later") or [])
        converted = empty_payload(phase)
        converted["meta"]["phase"] = phase
        converted["meta"]["tko_id"] = _stringify(legacy_meta.get("work_item_id"))
        converted["meta"]["derax_version"] = "legacy"
        converted["meta"]["timestamp"] = _stringify(legacy_meta.get("created_at"))
        converted["meta"]["source_chat_id"] = _stringify(legacy_meta.get("chat_id"))
        converted["meta"]["source_turn_id"] = ""
        converted["canonical_summary"] = _stringify(out.get("headline"))
        converted["intent"]["destination"] = _stringify(core.get("end_in_mind"))
        converted["intent"]["success_criteria"] = _listify(core.get("destination_conditions"))
        converted["intent"]["constraints"] = _listify(core.get("assumptions"))
        converted["intent"]["non_goals"] = _listify(core.get("non_goals"))
        converted["intent"]["assumptions"] = _listify(core.get("assumptions"))
        converted["intent"]["open_questions"] = _listify(core.get("ambiguities"))
        converted["explore"]["adjacent_ideas"] = _listify(core.get("adjacent_angles"))
        converted["explore"]["risks"] = _listify(core.get("risks"))
        converted["explore"]["tradeoffs"] = _listify(core.get("scope_changes"))
        converted["explore"]["reframes"] = _listify(core.get("ambiguities"))
        converted["parked_for_later"]["items"] = [
            {"title": str(v).strip(), "detail": ""}
            for v in parked
            if str(v).strip()
        ]
        return converted

    return out


def _normalise_alias_fields(payload: dict) -> dict:
    out = _as_dict(payload)
    meta = _as_dict(out.get("meta"))
    intent = _as_dict(out.get("intent"))
    explore = _as_dict(out.get("explore"))
    parked = _as_dict(out.get("parked_for_later"))
    core = _as_dict(out.get("core"))

    if not _stringify(meta.get("phase")):
        phase_guess = _stringify(out.get("phase")).upper()
        if phase_guess:
            meta["phase"] = phase_guess
    out["meta"] = meta

    if not _stringify(intent.get("destination")):
        intent["destination"] = _stringify(intent.get("end_in_mind"))
    if not _stringify(intent.get("destination")):
        intent["destination"] = _stringify(intent.get("goal"))
    if not _stringify(intent.get("destination")):
        intent["destination"] = _stringify(core.get("end_in_mind"))
    if not _stringify(intent.get("destination")):
        intent["destination"] = _stringify(out.get("destination"))
    if not _stringify(intent.get("destination")):
        intent["destination"] = _stringify(out.get("end_in_mind"))
    if not _listify(intent.get("success_criteria")):
        intent["success_criteria"] = _listify(intent.get("destination_conditions"))
    if not _listify(intent.get("success_criteria")):
        intent["success_criteria"] = _listify(core.get("destination_conditions"))
    if not _listify(intent.get("success_criteria")):
        intent["success_criteria"] = _listify(out.get("success_criteria"))
    if not _listify(intent.get("constraints")):
        intent["constraints"] = _listify(intent.get("limits"))
    if not _listify(intent.get("constraints")):
        intent["constraints"] = _listify(core.get("assumptions"))
    if not _listify(intent.get("constraints")):
        intent["constraints"] = _listify(out.get("constraints"))
    if not _listify(intent.get("non_goals")):
        intent["non_goals"] = _listify(intent.get("out_of_scope"))
    if not _listify(intent.get("non_goals")):
        intent["non_goals"] = _listify(core.get("non_goals"))
    if not _listify(intent.get("non_goals")):
        intent["non_goals"] = _listify(out.get("non_goals"))
    if not _listify(intent.get("assumptions")):
        intent["assumptions"] = _listify(intent.get("hypotheses"))
    if not _listify(intent.get("assumptions")):
        intent["assumptions"] = _listify(core.get("assumptions"))
    if not _listify(intent.get("assumptions")):
        intent["assumptions"] = _listify(out.get("assumptions"))
    if not _listify(intent.get("open_questions")):
        intent["open_questions"] = _listify(intent.get("questions"))
    if not _listify(intent.get("open_questions")):
        intent["open_questions"] = _listify(core.get("ambiguities"))
    if not _listify(intent.get("open_questions")):
        intent["open_questions"] = _listify(out.get("open_questions"))
    out["intent"] = intent

    if not _listify(explore.get("adjacent_ideas")):
        explore["adjacent_ideas"] = _listify(explore.get("adjacent_angles"))
    if not _listify(explore.get("adjacent_ideas")):
        explore["adjacent_ideas"] = _listify(core.get("adjacent_angles"))
    if not _listify(explore.get("adjacent_ideas")):
        explore["adjacent_ideas"] = _listify(out.get("adjacent_ideas"))
    if not _listify(explore.get("risks")):
        explore["risks"] = _listify(core.get("risks"))
    if not _listify(explore.get("risks")):
        explore["risks"] = _listify(out.get("risks"))
    if not _listify(explore.get("tradeoffs")):
        explore["tradeoffs"] = _listify(explore.get("scope_changes"))
    if not _listify(explore.get("tradeoffs")):
        explore["tradeoffs"] = _listify(core.get("scope_changes"))
    if not _listify(explore.get("tradeoffs")):
        explore["tradeoffs"] = _listify(out.get("tradeoffs"))
    if not _listify(explore.get("reframes")):
        explore["reframes"] = _listify(core.get("ambiguities"))
    if not _listify(explore.get("reframes")):
        explore["reframes"] = _listify(out.get("reframes"))
    out["explore"] = explore

    rows = parked.get("items")
    if not isinstance(rows, list):
        rows = []
    parked_rows = []
    for item in rows:
        if isinstance(item, dict):
            parked_rows.append(
                {
                    "title": _stringify(item.get("title")),
                    "detail": _stringify(item.get("detail")),
                }
            )
            continue
        text = _stringify(item)
        if text:
            parked_rows.append({"title": text, "detail": ""})
    parked["items"] = parked_rows
    out["parked_for_later"] = parked

    if not _stringify(out.get("canonical_summary")):
        destination_summary = _stringify(intent.get("destination"))
        if destination_summary:
            out["canonical_summary"] = " ".join(destination_summary.split()[:10])
    return out


def _normalise_artefacts(payload: dict) -> dict:
    out = _as_dict(payload)
    if "artefacts" not in out:
        return out
    artefacts = _as_dict(out.get("artefacts"))

    proposed_rows = []
    for item in list(artefacts.get("proposed") or []):
        if isinstance(item, dict):
            proposed_rows.append(
                {
                    "kind": _stringify(item.get("kind")),
                    "title": _stringify(item.get("title")),
                    "notes": _stringify(item.get("notes")),
                }
            )
            continue
        text = _stringify(item)
        if text:
            proposed_rows.append({"kind": "", "title": text, "notes": ""})

    generated_rows = []
    for item in list(artefacts.get("generated") or []):
        if isinstance(item, dict):
            generated_rows.append(
                {
                    "artefact_id": _stringify(item.get("artefact_id")),
                    "kind": _stringify(item.get("kind")),
                    "title": _stringify(item.get("title")),
                }
            )
            continue
        text = _stringify(item)
        if text:
            generated_rows.append({"artefact_id": "", "kind": "", "title": text})

    requirements_obj = {}
    raw_requirements = artefacts.get("requirements")
    for req_kind, req_values in _mapping_items(raw_requirements):
            kind = _stringify(req_kind)
            if not kind:
                continue
            requirements_obj[kind] = _listify(req_values)
    intake_obj = {}
    raw_intake = artefacts.get("intake")
    for intake_key, intake_value in _mapping_items(raw_intake):
            key = _stringify(intake_key)
            if not key:
                continue
            value = _as_dict(intake_value)
            status = _stringify(value.get("status")).upper() or "MISSING_INPUTS"
            reqs = _listify(value.get("requirements"))
            intake_obj[key] = {"status": status, "requirements": reqs}

    artefacts["proposed"] = proposed_rows
    artefacts["generated"] = generated_rows
    artefacts["requirements"] = requirements_obj
    artefacts["intake"] = intake_obj
    out["artefacts"] = artefacts
    return out


def _extract_strict_json_with_mode(text: str) -> tuple[dict | None, list[str], str]:
    raw = str(text or "")
    stripped = raw.strip()
    if not stripped:
        return None, ["Invalid JSON: empty response"], "empty"

    # Accept a pure JSON object.
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            payload = json.loads(stripped)
        except Exception as exc:
            return None, [f"Invalid JSON: {exc}"], "raw_object"
        if not isinstance(payload, dict):
            return None, ["Invalid JSON: top-level value must be an object"], "raw_object"
        return payload, [], "raw_object"

    # Accept a fenced JSON block.
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            header = str(lines[0] or "").strip().lower()
            trailer = str(lines[-1] or "").strip()
            if trailer == "```" and (header in {"```", "```json"}):
                body = "\n".join(lines[1:-1]).strip()
                try:
                    payload = json.loads(body)
                except Exception as exc:
                    return None, [f"Invalid JSON: {exc}"], "fenced_block"
                if not isinstance(payload, dict):
                    return None, ["Invalid JSON: top-level value must be an object"], "fenced_block"
                return payload, [], "fenced_block"

    # Accept a fenced JSON block anywhere in the response.
    for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)\s*```", stripped, flags=re.IGNORECASE):
        body = str(match.group(1) or "").strip()
        if not body:
            continue
        try:
            payload = json.loads(body)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload, [], "fenced_embedded"

    # Accept one JSON object embedded in prose.
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(stripped):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(stripped[idx:])
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj, [], "embedded_object"

    return None, ["Non-JSON content outside JSON object"], "non_json"


def extract_strict_json(text: str) -> tuple[dict | None, list[str]]:
    payload, errors, _mode = _extract_strict_json_with_mode(text)
    return payload, errors


def validate_derax_text(text: str) -> tuple[bool, dict | None, list[str]]:
    payload, parse_errors, parse_mode = _extract_strict_json_with_mode(text)
    if parse_errors:
        return False, None, [f"{str(e)} [parse={parse_mode}]" for e in list(parse_errors or [])]

    assert payload is not None
    payload = _normalise_payload_shape(payload)
    payload = _normalise_alias_fields(payload)
    payload = _normalise_artefacts(payload)
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

    errors.extend(_phase_policy_errors(payload))

    if errors:
        return False, payload, errors
    return True, payload, []


def build_correction_message(
    errors: list[str],
    *,
    phase: str = "DEFINE",
    manifest: dict | None = None,
    payload_optional: dict | None = None,
) -> str:
    resolved_phase = str(phase or "").strip().upper() or "DEFINE"
    phase_manifest = manifest if isinstance(manifest, dict) else {}
    if not phase_manifest:
        try:
            phase_manifest = get_phase_manifest(resolved_phase)
        except Exception:
            phase_manifest = {}
    template = empty_payload(resolved_phase)
    template_json = json.dumps(template, ensure_ascii=True, indent=2)
    lines = [
        "Return JSON only.",
        "No markdown. No commentary.",
        "Use the canonical schema keys exactly.",
        f"Set meta.phase to: {resolved_phase}",
    ]
    if resolved_phase == "DEFINE":
        lines.extend(
            [
                "Set intent.success_criteria to []",
                "Set artefacts.proposed to []",
                "Trim intent.open_questions to max 3",
                "Trim parked_for_later.items to max 3",
                "Trim intent.assumptions to max 1 (prefer HYPOTHESIS:...)",
                "Set canonical_summary to <=10 words or empty",
            ]
        )
    if payload_optional is not None and isinstance(payload_optional, dict):
        lines.append("Correct the last payload in-place to satisfy every rule.")
    lines.extend(
        [
        "Use this JSON shape exactly (fill values, keep keys):",
        template_json,
        ]
    )
    if phase_manifest:
        required_paths = list(phase_manifest.get("required_paths") or [])
        if required_paths:
            lines.append("Required non-empty paths:")
            for req in required_paths:
                lines.append(f"- {req}")
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
def derax_json_correction_prompt(error_text: str = "", phase: str = "DEFINE", payload: dict | None = None) -> str:
    errs: list[str] = []
    raw = str(error_text or "").strip()
    if raw:
        if ";" in raw:
            errs.extend([part.strip() for part in raw.split(";") if part.strip()])
        else:
            errs.append(raw)
    return build_correction_message(errs, phase=phase, payload_optional=payload)
