# -*- coding: utf-8 -*-
"""DERAX APPROVE evaluator: warnings-but-not-blocking advisory checks.

This module reports warnings/errors for human judgement. It does not enforce
workflow stops by itself.
"""

from __future__ import annotations

import re

from chats.services.derax.contracts import get_phase_manifest


_MECHANISM_KEYWORDS = (
    "detailed",
    "owners",
    "weeks",
    "stage-gated",
    "thresholds",
    "bands",
)


def _as_text(value) -> str:
    return str(value or "").strip()


def _list_str(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _find_missing_paths(payload: dict, paths: list[str]) -> list[str]:
    missing: list[str] = []
    for path in paths:
        node = payload
        ok = True
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                ok = False
                break
            node = node.get(part)
        if not ok:
            missing.append(path)
            continue
        if isinstance(node, str) and not node.strip():
            missing.append(path)
        elif isinstance(node, list) and len(_list_str(node)) == 0:
            missing.append(path)
    return missing


def _contains_keyword(text: str, words: tuple[str, ...]) -> bool:
    lower = _as_text(text).lower()
    if not lower:
        return False
    return any(word in lower for word in words)


def _has_short_session_signal(payload: dict) -> bool:
    meta_text = " ".join(
        [
            _as_text((payload.get("meta") or {}).get("phase")),
            _as_text((payload.get("meta") or {}).get("timestamp")),
            _as_text((payload.get("meta") or {}).get("tko_id")),
        ]
    ).lower()
    constraints = " ".join(_list_str((payload.get("intent") or {}).get("constraints"))).lower()
    all_text = f"{meta_text} {constraints}".strip()
    return ("2 hour" in all_text) or ("2-hour" in all_text)


def _intent_texts(payload: dict) -> list[str]:
    intent = payload.get("intent") or {}
    out = [_as_text(intent.get("destination"))]
    for key in ("success_criteria", "constraints", "non_goals", "assumptions", "open_questions"):
        out.extend(_list_str(intent.get(key)))
    return [v for v in out if v]


def _contradicts_non_goals(destination: str, non_goals: list[str]) -> bool:
    d = destination.lower()
    if not d:
        return False
    for goal in non_goals:
        g = goal.lower()
        if not g:
            continue
        # Deterministic heuristic: "not/no X" in non-goal while destination includes X.
        m = re.search(r"\b(?:not|no)\s+([a-z0-9 _-]{3,})", g)
        if m:
            banned = m.group(1).strip()
            if banned and banned in d:
                return True
    return False


def _numeric_specificity_count(texts: list[str]) -> int:
    blob = " ".join(texts)
    return len(re.findall(r"\b\d+(?:\.\d+)?\b", blob))


def _iter_contention_sources(payload: dict) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    explore = payload.get("explore") or {}
    for key in ("adjacent_ideas", "risks", "tradeoffs", "reframes"):
        for item in _list_str(explore.get(key)):
            rows.append((f"explore.{key}", item))
    parked = (payload.get("parked_for_later") or {}).get("items")
    if isinstance(parked, list):
        for item in parked:
            if not isinstance(item, dict):
                continue
            title = _as_text(item.get("title"))
            detail = _as_text(item.get("detail"))
            text = f"{title} {detail}".strip()
            if text:
                rows.append(("parked_for_later.items", text))
    return rows


def _is_destination_conflict(destination: str, text: str) -> bool:
    d = destination.lower()
    t = text.lower()
    if not d or not t:
        return False
    # Deterministic conflict trigger for explicit negation patterns.
    return ("not " in t or "do not" in t or "no " in t) and any(
        token in t for token in [tok for tok in re.findall(r"[a-z0-9]+", d) if len(tok) >= 5]
    )


def evaluate_approval(payload: dict) -> dict:
    """Evaluate APPROVE payload and return advisory findings."""
    warnings: list[str] = []
    errors: list[str] = []
    suggested_action: list[str] = []

    canonical_summary = _as_text(payload.get("canonical_summary"))
    intent = payload.get("intent") or {}
    destination = _as_text(intent.get("destination"))
    success_criteria = _list_str(intent.get("success_criteria"))
    non_goals = _list_str(intent.get("non_goals"))

    if not canonical_summary:
        warnings.append("Missing canonical_summary")
    if not destination:
        errors.append("intent.destination is missing")
    if not success_criteria:
        warnings.append("intent.success_criteria is empty")

    if destination and _contradicts_non_goals(destination, non_goals):
        errors.append("intent.destination contradicts non_goals")
        suggested_action.append("Align destination wording with non-goals")

    refine_required = list(get_phase_manifest("REFINE").get("required_paths", []))
    missing_refine = _find_missing_paths(payload, refine_required)
    for path in missing_refine:
        if path == "intent.destination":
            if "intent.destination is missing" not in errors:
                errors.append("intent.destination is missing")
        else:
            warnings.append(f"Missing REFINE required path: {path}")

    if _has_short_session_signal(payload):
        intent_texts = _intent_texts(payload)
        if any(_contains_keyword(text, _MECHANISM_KEYWORDS) for text in intent_texts):
            warnings.append("Mechanism detail present for 2-hour session - compress to principle level")
            suggested_action.append("Move mechanism detail to parked_for_later")
        if _numeric_specificity_count(intent_texts) >= 4:
            warnings.append("High numeric specificity in short session context - compress detail")
            suggested_action.append("Keep only decision-critical thresholds")

    for path, text in _iter_contention_sources(payload):
        if destination and _is_destination_conflict(destination, text):
            errors.append(f"conflict: {path} contradicts earlier intent")
            warnings.append("Add explicit conflict resolution question in intent.open_questions")
            suggested_action.append("Ask one resolution question in intent.open_questions")
            break

    schema_ok = "no" if errors else "yes"
    # Deterministic de-dup preserving order.
    def _dedupe(values: list[str]) -> list[str]:
        out: list[str] = []
        seen = set()
        for v in values:
            if v in seen:
                continue
            seen.add(v)
            out.append(v)
        return out

    return {
        "schema_ok": schema_ok,
        "warnings": _dedupe(warnings),
        "errors": _dedupe(errors),
        "suggested_action": _dedupe(suggested_action),
    }


def apply_approval_results_to_payload(payload: dict, results: dict) -> dict:
    new_payload = dict(payload or {})
    validation = dict(new_payload.get("validation") or {})
    validation["schema_ok"] = _as_text((results or {}).get("schema_ok")) or "yes"
    validation["errors"] = list((results or {}).get("errors") or [])
    validation["warnings"] = list((results or {}).get("warnings") or [])
    new_payload["validation"] = validation
    return new_payload

