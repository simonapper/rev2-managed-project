from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path


def _extract_section(text: str, header: str) -> str:
    marker = "## " + header
    start = text.find(marker)
    if start == -1:
        return ""
    start = text.find("\n", start) + 1
    next_idx = text.find("## ", start)
    if next_idx == -1:
        next_idx = len(text)
    return (text[start:next_idx] or "").strip()


def _extract_subsection(text: str, header: str) -> str:
    marker = "### " + header
    start = text.find(marker)
    if start == -1:
        return ""
    start = text.find("\n", start) + 1
    next_idx = text.find("### ", start)
    if next_idx == -1:
        next_idx = len(text)
    return (text[start:next_idx] or "").strip()


@lru_cache(maxsize=1)
def _agents_text() -> str:
    try:
        root = Path(__file__).resolve().parents[1]
        return (root / "AGENTS.md").read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def get_conference_seed_excerpt(marker: str) -> str:
    text = _agents_text()
    if not text:
        return ""
    defs = _extract_section(text, "Artefact Definitions")
    fmt = _extract_section(text, "Artefact Formatting Rules")
    structures = _extract_section(text, "Artefact Structures (sections optional; do not add filler)")
    structure = ""
    marker_u = (marker or "").strip().upper()
    if marker_u == "INTENT":
        structure = _extract_subsection(structures, "CKO")
    elif marker_u == "ROUTE":
        structure = _extract_subsection(structures, "WKO")
    elif marker_u == "EXECUTE":
        structure = _extract_subsection(structures, "WKO")
    elif marker_u == "COMPLETE":
        structure = _extract_subsection(structures, "PKO")

    parts = []
    if defs:
        parts.append(defs)
    if fmt:
        parts.append(fmt)
    if structure:
        parts.append("Structure\n" + structure)
    return "\n\n".join([p for p in parts if (p or "").strip()]).strip()


def build_cko_seed_text(content_json: dict) -> str:
    if not isinstance(content_json, dict):
        return ""
    sections = [
        ("CANONICAL SUMMARY", "canonical_summary"),
        ("SCOPE", "scope"),
        ("STATEMENT (ANCHOR)", "statement"),
        ("SUPPORTING BASIS", "supporting_basis"),
        ("ASSUMPTIONS", "assumptions"),
        ("ALTERNATIVES CONSIDERED", "alternatives_considered"),
        ("UNCERTAINTIES / LIMITS", "uncertainties_limits"),
        ("PROVENANCE", "provenance"),
    ]
    out = []
    for header, key in sections:
        val = str(content_json.get(key) or "").strip()
        if not val:
            continue
        out.append("# " + header)
        out.append(val)
        out.append("")
    if not out:
        return ""
    return "\n".join(out).rstrip("\n") + "\n"


def get_pdo_schema_text() -> str:
    return (
        "{\n"
        "  \"pdo_summary\": \"\",\n"
        "  \"cko_alignment\": {\n"
        "    \"stage1_inputs_match\": \"\",\n"
        "    \"final_outputs_match\": \"\"\n"
        "  },\n"
        "  \"planning_purpose\": \"\",\n"
        "  \"planning_constraints\": \"\",\n"
        "  \"assumptions\": \"\",\n"
        "  \"stages\": [\n"
        "    {\n"
        "      \"stage_id\": \"S1\",\n"
        "      \"stage_number\": 1,\n"
        "      \"status\": \"\",\n"
        "      \"title\": \"\",\n"
        "      \"purpose\": \"\",\n"
        "      \"inputs\": \"\",\n"
        "      \"stage_process\": \"\",\n"
        "      \"outputs\": \"\",\n"
        "      \"assumptions\": \"\",\n"
        "      \"duration_estimate\": \"\",\n"
        "      \"risks_notes\": \"\"\n"
        "    }\n"
        "  ]\n"
        "}\n"
    )


def normalise_pdo_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    def _str(val):
        return str(val or "").strip()

    cko = payload.get("cko_alignment")
    if not isinstance(cko, dict):
        cko = {}
    stage1 = _str(cko.get("stage1_inputs_match") or payload.get("cko_alignment_stage1_inputs_match"))
    final = _str(cko.get("final_outputs_match") or payload.get("cko_alignment_final_outputs_match"))

    out = {
        "pdo_summary": _str(payload.get("pdo_summary")),
        "cko_alignment": {
            "stage1_inputs_match": stage1,
            "final_outputs_match": final,
        },
        "planning_purpose": _str(payload.get("planning_purpose")),
        "planning_constraints": _str(payload.get("planning_constraints")),
        "assumptions": _str(payload.get("assumptions")),
        "stages": [],
    }

    def _stage_score(stage_obj: dict) -> int:
        keys = (
            "status",
            "title",
            "purpose",
            "inputs",
            "stage_process",
            "outputs",
            "assumptions",
            "duration_estimate",
            "risks_notes",
        )
        score = 0
        for key in keys:
            if str(stage_obj.get(key) or "").strip():
                score += 1
        return score

    stage_rows = {}
    stage_order = []
    stages = payload.get("stages")
    if isinstance(stages, list):
        for idx, item in enumerate(stages, start=1):
            if not isinstance(item, dict):
                continue
            stage_number = item.get("stage_number")
            try:
                stage_number = int(stage_number)
            except Exception:
                stage_number = idx
            stage_id = str(item.get("stage_id") or "").strip()
            if not stage_id:
                stage_id = f"S{stage_number}"
            outputs_val = item.get("outputs", item.get("key_deliverables", ""))
            if isinstance(outputs_val, list):
                outputs_val = "\n".join([str(x).strip() for x in outputs_val if str(x).strip()])
            stage_process = item.get("stage_process", item.get("description", ""))
            assumptions = item.get("assumptions", item.get("key_variables", ""))
            stage_obj = {
                "stage_id": stage_id,
                "stage_number": stage_number,
                "status": _str(item.get("status")),
                "title": _str(item.get("title")),
                "purpose": _str(item.get("purpose")),
                "inputs": _str(item.get("inputs")),
                "stage_process": _str(stage_process),
                "outputs": _str(outputs_val),
                "assumptions": _str(assumptions),
                "duration_estimate": _str(item.get("duration_estimate")),
                "risks_notes": _str(item.get("risks_notes")),
            }
            key = stage_id or f"S{stage_number}"
            if key not in stage_rows:
                stage_rows[key] = stage_obj
                stage_order.append(key)
                continue
            # Keep the more complete duplicate when stage keys collide.
            if _stage_score(stage_obj) >= _stage_score(stage_rows[key]):
                stage_rows[key] = stage_obj
    out["stages"] = [stage_rows[k] for k in stage_order if k in stage_rows]
    return out


def build_execute_seed(route_payload: dict) -> dict:
    normalised = normalise_pdo_payload(route_payload or {})
    stages = []
    work_items = []
    outputs = []
    output_ids = set()

    def _split_outputs(val: str) -> list[str]:
        text = (val or "").strip()
        if not text:
            return []
        if re.search(r"\b\d+\.\s+", text):
            parts = re.split(r"\s*(?:\d+\.)\s+", text)
            parts = [p.strip() for p in parts if p.strip()]
        else:
            parts = [line.strip() for line in text.splitlines() if line.strip()]
        cleaned = []
        for p in parts:
            p = re.sub(r"^\d+\.\s*", "", p).strip()
            if p:
                cleaned.append(p)
        return cleaned

    def _new_output_id(stage_id: str, idx: int) -> str:
        base = f"O{stage_id}-{idx}"
        if base not in output_ids:
            output_ids.add(base)
            return base
        suffix = 2
        while f"{base}-{suffix}" in output_ids:
            suffix += 1
        final = f"{base}-{suffix}"
        output_ids.add(final)
        return final

    for item in normalised.get("stages", []):
        if not isinstance(item, dict):
            continue
        stage_number = item.get("stage_number")
        stage_id = str(item.get("stage_id") or "").strip()
        if not stage_id and stage_number:
            stage_id = f"S{stage_number}"
        stage_outputs = _split_outputs(str(item.get("outputs") or "").strip())
        outputs_due = []
        outputs_status = []
        for idx, title in enumerate(stage_outputs, start=1):
            output_id = _new_output_id(stage_id, idx)
            outputs.append(
                {
                    "output_id": output_id,
                    "title": title,
                    "status": "not_started",
                    "stage_id": stage_id,
                }
            )
            outputs_due.append(
                {
                    "output_id": output_id,
                    "title": title,
                }
            )
            outputs_status.append(
                {
                    "output_id": output_id,
                    "status": "not_started",
                }
            )
        route_items = item.get("work_items") if isinstance(item.get("work_items"), list) else []
        stage_work_items = []
        for idx, wi in enumerate(route_items, start=1):
            if not isinstance(wi, dict):
                continue
            wi_id = str(wi.get("wi_id") or "").strip() or f"W{stage_id}-{idx}"
            stage_work_items.append(
                {
                    "wi_id": wi_id,
                    "title": str(wi.get("title") or "").strip(),
                    "status": "not_started",
                    "stage_id": stage_id,
                    "stage_number": stage_number,
                }
            )
        work_items.extend(stage_work_items)
        stage_entry = {
            "stage_id": stage_id,
            "stage_number": stage_number,
            "title": str(item.get("title") or "").strip(),
            "purpose": str(item.get("purpose") or "").strip(),
            "inputs": str(item.get("inputs") or "").strip(),
            "stage_process": str(item.get("stage_process") or "").strip(),
            "outputs": str(item.get("outputs") or "").strip(),
            "assumptions": str(item.get("assumptions") or "").strip(),
            "duration_estimate": str(item.get("duration_estimate") or "").strip(),
            "risks_notes": str(item.get("risks_notes") or "").strip(),
            "status": "not_started",
            "started_at": "",
            "completed_at": "",
            "outputs_due": outputs_due,
            "outputs_status": outputs_status,
            "work_items": stage_work_items,
            "decisions": [],
            "blockers": [],
            "evidence": [],
        }
        stages.append(stage_entry)
    current_stage_id = stages[0]["stage_id"] if stages else ""
    outputs = sorted(outputs, key=lambda x: str(x.get("output_id") or ""))
    work_items = sorted(work_items, key=lambda x: str(x.get("wi_id") or ""))
    return {
        "outputs": outputs,
        "stages": stages,
        "work_items": work_items,
        "current_stage_id": current_stage_id,
        "overall_status": "active",
        "blockers": [],
        "decisions": [],
        "notes": "",
    }


def _canonical_json(payload: dict) -> str:
    return json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash_json(payload: dict) -> str:
    import hashlib

    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def seed_execute_from_route(route_payload: dict) -> dict:
    normalised = normalise_pdo_payload(route_payload or {})
    exec_payload = build_execute_seed(normalised)
    return {
        "artefact_type": "EXECUTION_STATE",
        "marker": "EXECUTE",
        "version": 1,
        "source_route": {
            "route_version": int(route_payload.get("version") or 1) if isinstance(route_payload, dict) else 1,
            "route_hash": _hash_json(normalised),
        },
        **exec_payload,
        "next_review_questions": [],
        "today_focus": "",
    }


def merge_execute_payload(existing: dict, incoming: dict) -> dict:
    existing = existing or {}
    incoming = incoming or {}

    def _keep(existing_val, incoming_val):
        if incoming_val is None or incoming_val == "":
            return existing_val
        return incoming_val

    out = dict(existing)
    for key in [
        "artefact_type",
        "marker",
        "version",
        "overall_status",
        "current_stage_id",
        "notes",
        "today_focus",
    ]:
        if key in incoming:
            out[key] = _keep(out.get(key), incoming.get(key))

    if "blockers" in incoming and incoming.get("blockers"):
        out["blockers"] = incoming.get("blockers")
    if "decisions" in incoming and incoming.get("decisions"):
        out["decisions"] = incoming.get("decisions")
    if "next_review_questions" in incoming and incoming.get("next_review_questions"):
        out["next_review_questions"] = incoming.get("next_review_questions")

    def _output_key(item: dict) -> str:
        return str(item.get("output_id") or item.get("title") or "")

    existing_outputs = {_output_key(o): o for o in (out.get("outputs") or []) if isinstance(o, dict)}
    if "outputs" in incoming and isinstance(incoming.get("outputs"), list):
        for o in incoming["outputs"]:
            if not isinstance(o, dict):
                continue
            oid = _output_key(o)
            if oid in existing_outputs:
                merged = dict(existing_outputs[oid])
                for k, v in o.items():
                    merged[k] = _keep(merged.get(k), v)
                existing_outputs[oid] = merged
            else:
                existing_outputs[oid] = o
    out["outputs"] = sorted(existing_outputs.values(), key=lambda x: str(x.get("output_id") or ""))

    existing_wi = {w.get("wi_id"): w for w in (out.get("work_items") or []) if isinstance(w, dict)}
    if "work_items" in incoming and isinstance(incoming.get("work_items"), list):
        for w in incoming["work_items"]:
            if not isinstance(w, dict):
                continue
            wid = w.get("wi_id") or w.get("title")
            if wid in existing_wi:
                merged = dict(existing_wi[wid])
                for k, v in w.items():
                    merged[k] = _keep(merged.get(k), v)
                existing_wi[wid] = merged
            else:
                existing_wi[wid] = w
    out["work_items"] = sorted(existing_wi.values(), key=lambda x: str(x.get("wi_id") or x.get("title") or ""))

    def _stage_key(stage: dict) -> str:
        if not isinstance(stage, dict):
            return ""
        sid = str(stage.get("stage_id") or "").strip()
        if sid:
            return sid
        try:
            num = int(stage.get("stage_number") or 0)
        except Exception:
            num = 0
        if num > 0:
            return f"S{num}"
        return ""

    existing_stages = {}
    for stage in (out.get("stages") or []):
        if not isinstance(stage, dict):
            continue
        skey = _stage_key(stage)
        if not skey:
            # Drop legacy placeholder rows that have no stable stage key.
            continue
        existing_stages[skey] = stage

    if "stages" in incoming and isinstance(incoming.get("stages"), list):
        for s in incoming["stages"]:
            if not isinstance(s, dict):
                continue
            sid = _stage_key(s)
            if not sid:
                continue
            if sid in existing_stages:
                merged = dict(existing_stages[sid])
                for k, v in s.items():
                    merged[k] = _keep(merged.get(k), v)
                if not str(merged.get("stage_id") or "").strip():
                    merged["stage_id"] = sid
                existing_stages[sid] = merged
            else:
                added = dict(s)
                if not str(added.get("stage_id") or "").strip():
                    added["stage_id"] = sid
                existing_stages[sid] = added
    out["stages"] = sorted(existing_stages.values(), key=lambda x: int(x.get("stage_number") or 0))
    return out
