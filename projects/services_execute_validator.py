from __future__ import annotations

import json


def build_execute_conference_seed(project, route_anchor, execute_anchor) -> str:
    parts = []
    parts.append("This is a controlled Review Conference.")
    parts.append("Purpose: maintain the EXECUTE ledger derived from ROUTE.")
    parts.append("Start in human readable form.")
    parts.append("Use the EXECUTE sections.")
    parts.append("Sections: Summary, Outputs, Stages.")
    parts.append("Use ROUTE as the source.")
    parts.append("Ask questions. Then update EXECUTE.")
    parts.append("Do not invent stages or work items.")
    parts.append("Do not change stage_id or stage_number.")
    parts.append("If the plan needs change, propose a ROUTE revision.")
    parts.append("Do not delete decisions, evidence, or history fields.")
    parts.append("When the user says ready, return JSON only in one json block.")
    parts.append("Short sentences only.")
    parts.append("One idea per sentence.")
    parts.append("Avoid qualifiers.")
    parts.append("Avoid comma-joined clauses.")
    return "\n".join(parts)


def build_execute_stage_seed(project, stage_key, route_anchor, execute_anchor, execute_stage, route_stage=None) -> str:
    parts = []
    parts.append("This is a controlled Review Conference (stage).")
    parts.append("Purpose: update one stage only.")
    parts.append("Keep changes local.")
    parts.append("Do not change stage_id or stage_number.")
    parts.append("Start in human readable form.")
    parts.append("Use the ROUTE stage as the source.")
    parts.append("Update the EXECUTE stage only.")
    parts.append("When the user says ready, return the full EXECUTE JSON in one json block.")
    parts.append("Short sentences only.")
    parts.append("One idea per sentence.")
    parts.append("Avoid qualifiers.")
    parts.append("Avoid comma-joined clauses.")
    if isinstance(execute_stage, dict) and execute_stage:
        parts.append("Stage context:")
        parts.append(json.dumps(execute_stage, indent=2, ensure_ascii=True))
    if isinstance(route_stage, dict) and route_stage:
        parts.append("Route stage:")
        parts.append(json.dumps(route_stage, indent=2, ensure_ascii=True))
    return "\n".join(parts)


def validate_execute_update(route_json: dict, current_execute: dict, proposed_execute: dict):
    errors = []
    if not isinstance(proposed_execute, dict):
        return False, ["Proposed EXECUTE is not a JSON object."]
    required_keys = ["artefact_type", "marker", "version", "source_route", "outputs", "stages"]
    for k in required_keys:
        if k not in proposed_execute:
            errors.append(f"Missing key: {k}")
    if proposed_execute.get("marker") != "EXECUTE":
        errors.append("marker must be EXECUTE.")

    route_stages = route_json.get("stages") if isinstance(route_json, dict) else []
    exec_stages = proposed_execute.get("stages") if isinstance(proposed_execute.get("stages"), list) else []

    def _stage_id(s):
        sid = s.get("stage_id")
        if sid:
            return str(sid)
        num = s.get("stage_number")
        return f"S{num}" if num is not None else ""

    route_ids = {_stage_id(s) for s in route_stages if isinstance(s, dict)}
    exec_ids = {_stage_id(s) for s in exec_stages if isinstance(s, dict)}
    if route_ids and exec_ids != route_ids:
        errors.append("Stages must match ROUTE stage_id set.")

    current_ids = {_stage_id(s) for s in (current_execute.get("stages") or []) if isinstance(s, dict)}
    if current_ids and exec_ids != current_ids:
        errors.append("Stages must match current EXECUTE stage_id set.")

    for s in exec_stages:
        if not isinstance(s, dict):
            continue
        sid = _stage_id(s)
        if not sid:
            errors.append("Each stage must have stage_id or stage_number.")

    def _wi_ids(payload):
        ids = set()
        for w in payload.get("work_items") or []:
            if isinstance(w, dict) and w.get("wi_id"):
                ids.add(w.get("wi_id"))
        return ids

    if current_execute:
        cur_wi = _wi_ids(current_execute)
        new_wi = _wi_ids(proposed_execute)
        if cur_wi and (new_wi - cur_wi):
            errors.append("New work items are not allowed.")
        if cur_wi and (cur_wi - new_wi):
            errors.append("Existing work items cannot be removed.")

    def _has_list(payload, key):
        v = payload.get(key)
        return isinstance(v, list)

    if current_execute:
        if _has_list(current_execute, "decisions") and not _has_list(proposed_execute, "decisions"):
            errors.append("decisions must not be dropped.")
        if _has_list(current_execute, "evidence") and not _has_list(proposed_execute, "evidence"):
            errors.append("evidence must not be dropped.")

    return len(errors) == 0, errors


def merge_execute_update(route_json: dict, current_execute: dict, proposed_execute: dict) -> dict:
    out = dict(current_execute or {})
    for key, val in (proposed_execute or {}).items():
        if val is None or val == "":
            continue
        out[key] = val
    if "decisions" not in proposed_execute and "decisions" in current_execute:
        out["decisions"] = current_execute.get("decisions")
    if "evidence" not in proposed_execute and "evidence" in current_execute:
        out["evidence"] = current_execute.get("evidence")
    return out
