# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
import io
import zipfile
import copy
from xml.etree import ElementTree as ET

from django.contrib import messages
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from projects.models import AuditLog, ProjectDocument, WorkItem
from chats.models import ContractText
from projects.services.context_resolution import resolve_effective_context
from projects.services_project_membership import accessible_projects_qs
from chats.services.contracts.pipeline import ContractContext
from chats.services.contracts.phase_resolver import resolve_phase_contract
from chats.services.contracts.texts import resolve_contract_text
from chats.services.derax.contracts import DERAX_PHASES, build_phase_contract_text
from chats.services.derax.persist import persist_derax_payload
from chats.services.derax.validate import derax_json_correction_prompt, validate_derax_response, validate_derax_text
from chats.services.derax.schema import empty_payload, validate_structural
from chats.services.derax.phase_rules import check_required_nonempty
from chats.services.derax.compile import persist_compiled_cko
from chats.services.derax.audit import persist_derax_project_audit
from chats.services.derax.generate import generate_artefacts_from_execute_payload, execute_export_capabilities
from chats.services.llm import generate_text


def _is_ajax(request) -> bool:
    return str(request.headers.get("x-requested-with") or "").lower() == "xmlhttprequest"


def _primary_work_item_for_project(project) -> WorkItem:
    work_item = (
        WorkItem.objects
        .filter(project=project, is_primary=True)
        .order_by("-updated_at", "-id")
        .first()
    )
    if work_item is not None:
        return work_item

    fallback = (
        WorkItem.objects
        .filter(project=project)
        .order_by("-updated_at", "-id")
        .first()
    )
    if fallback is not None:
        fallback.is_primary = True
        fallback.save(update_fields=["is_primary", "updated_at"])
        return fallback

    work_item = WorkItem.create_minimal(
        project=project,
        title=str(getattr(project, "name", "") or "")[:200],
        active_phase=WorkItem.PHASE_DEFINE,
    )
    work_item.is_primary = True
    work_item.save(update_fields=["is_primary", "updated_at"])
    return work_item


def _latest_define_assistant_text(work_item: WorkItem) -> str:
    history_entries = [h for h in list(work_item.derax_define_history or []) if isinstance(h, dict)]
    for row in reversed(history_entries):
        if str(row.get("role") or "").strip().lower() != "assistant":
            continue
        text = str(row.get("text") or "").strip()
        if text:
            return text
    return ""


def _latest_explore_assistant_text(work_item: WorkItem) -> str:
    history_entries = [h for h in list(work_item.derax_explore_history or []) if isinstance(h, dict)]
    for row in reversed(history_entries):
        if str(row.get("role") or "").strip().lower() != "assistant":
            continue
        text = str(row.get("text") or "").strip()
        if text:
            return text
    return ""


def _latest_explore_input_text(work_item: WorkItem) -> str:
    return _extract_end_in_mind(_latest_explore_assistant_text(work_item))


def _extract_end_in_mind(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            intent = dict(payload.get("intent") or {})
            destination = str(intent.get("destination") or "").strip()
            if destination:
                return destination
            core = dict(payload.get("core") or {})
            eim = str(core.get("end_in_mind") or "").strip()
            if eim:
                return eim
    except Exception:
        pass
    return raw


def _readable_derax_text(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except Exception:
        return raw
    if not isinstance(payload, dict):
        return raw

    lines = []
    meta = dict(payload.get("meta") or {})
    phase = str(meta.get("phase") or payload.get("phase") or "").strip().upper()
    if phase:
        lines.append(f"Phase: {phase}")

    artefacts = dict(payload.get("artefacts") or {})
    proposed = []
    for row in list(artefacts.get("proposed") or []):
        if isinstance(row, dict):
            proposed.append(
                {
                    "kind": str(row.get("kind") or "").strip(),
                    "title": str(row.get("title") or "").strip(),
                    "notes": str(row.get("notes") or "").strip(),
                }
            )
        else:
            text_row = str(row or "").strip()
            if text_row:
                proposed.append({"kind": "", "title": text_row, "notes": ""})
    generated = []
    for row in list(artefacts.get("generated") or []):
        if isinstance(row, dict):
            generated.append(
                {
                    "artefact_id": str(row.get("artefact_id") or "").strip(),
                    "kind": str(row.get("kind") or "").strip(),
                    "title": str(row.get("title") or "").strip(),
                }
            )
        else:
            text_row = str(row or "").strip()
            if text_row:
                generated.append({"artefact_id": "", "kind": "", "title": text_row})
    requirements = {}
    for req_kind, req_rows in dict(artefacts.get("requirements") or {}).items():
        key = str(req_kind or "").strip()
        if not key:
            continue
        rows = _as_list_of_str(req_rows)
        if rows:
            requirements[key] = rows
    intake = {}
    for idx_key, intake_row in dict(artefacts.get("intake") or {}).items():
        key = str(idx_key or "").strip()
        if not key:
            continue
        row = _as_dict(intake_row)
        status = str(row.get("status") or "").strip().upper() or "MISSING_INPUTS"
        reqs = _as_list_of_str(row.get("requirements"))
        intake[key] = {"status": status, "requirements": reqs}
    is_execute = phase == WorkItem.PHASE_EXECUTE
    if is_execute:
        if proposed:
            lines.append("Artefacts proposed:")
            for row in proposed:
                kind = str(row.get("kind") or "").strip()
                title = str(row.get("title") or "").strip()
                notes = str(row.get("notes") or "").strip()
                if kind and title:
                    lines.append(f"- {kind}: {title}")
                elif title:
                    lines.append(f"- {title}")
                elif kind:
                    lines.append(f"- {kind}")
                if notes:
                    lines.append(f"  notes: {notes}")
        if generated:
            lines.append("Artefacts generated:")
            for row in generated:
                kind = str(row.get("kind") or "").strip()
                title = str(row.get("title") or "").strip()
                artefact_id = str(row.get("artefact_id") or "").strip()
                label = f"{kind}: {title}" if kind and title else (title or kind or "(unnamed)")
                if artefact_id:
                    lines.append(f"- {label} (id {artefact_id})")
                else:
                    lines.append(f"- {label}")
        if requirements:
            lines.append("Required inputs by artefact:")
            for req_kind, req_rows in requirements.items():
                lines.append(f"- {req_kind}:")
                for req in req_rows:
                    lines.append(f"  - {req}")
        if intake:
            lines.append("Per-document intake status:")
            for idx_key, row in intake.items():
                status = str(row.get("status") or "").strip().upper()
                lines.append(f"- doc {idx_key}: {status or 'MISSING_INPUTS'}")
                for req in list(row.get("requirements") or []):
                    lines.append(f"  - {req}")
        if not proposed and not generated:
            lines.append("No execute artefacts returned.")
            lines.append("Ask for explicit artefacts.proposed with kind/title/notes.")
        return "\n".join(lines).strip() or raw

    intent = dict(payload.get("intent") or {})
    if intent:
        destination = str(intent.get("destination") or "").strip()
        if destination:
            lines.append(f"End in mind: {destination}")
        for label, key in (
            ("Success criteria", "success_criteria"),
            ("Constraints", "constraints"),
            ("Non-goals", "non_goals"),
            ("Open questions", "open_questions"),
        ):
            values = [str(v).strip() for v in list(intent.get(key) or []) if str(v).strip()]
            if values:
                lines.append(f"{label}:")
                lines.extend([f"- {v}" for v in values])

    explore = dict(payload.get("explore") or {})
    if explore:
        for label, key in (
            ("Adjacent ideas", "adjacent_ideas"),
            ("Risks", "risks"),
            ("Trade-offs", "tradeoffs"),
            ("Reframes", "reframes"),
        ):
            values = [str(v).strip() for v in list(explore.get(key) or []) if str(v).strip()]
            if values:
                lines.append(f"{label}:")
                lines.extend([f"- {v}" for v in values])

    core = dict(payload.get("core") or {})
    if core:
        end_in_mind = str(core.get("end_in_mind") or "").strip()
        if end_in_mind:
            lines.append(f"End in mind: {end_in_mind}")
        for label, key in (
            ("Destination conditions", "destination_conditions"),
            ("Non-goals", "non_goals"),
            ("Assumptions", "assumptions"),
            ("Ambiguities", "ambiguities"),
            ("Risks", "risks"),
            ("Scope changes", "scope_changes"),
        ):
            values = [str(v).strip() for v in list(core.get(key) or []) if str(v).strip()]
            if values:
                lines.append(f"{label}:")
                lines.extend([f"- {v}" for v in values])

    parked_rows = list((dict(payload.get("parked_for_later") or {})).get("items") or [])
    if parked_rows:
        lines.append("Parked for later:")
        for row in parked_rows:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            detail = str(row.get("detail") or "").strip()
            if title and detail:
                lines.append(f"- {title}: {detail}")
            elif title:
                lines.append(f"- {title}")
            elif detail:
                lines.append(f"- {detail}")

    parked_legacy = [str(v).strip() for v in list(payload.get("parked") or []) if str(v).strip()]
    if parked_legacy:
        lines.append("Parked for later:")
        lines.extend([f"- {v}" for v in parked_legacy])

    if proposed and not is_execute:
        lines.append("Artefacts proposed:")
        for row in proposed:
            kind = str(row.get("kind") or "").strip()
            title = str(row.get("title") or "").strip()
            notes = str(row.get("notes") or "").strip()
            if kind and title:
                lines.append(f"- {kind}: {title}")
            elif title:
                lines.append(f"- {title}")
            elif kind:
                lines.append(f"- {kind}")
            if notes:
                lines.append(f"  notes: {notes}")
    if generated and not is_execute:
        lines.append("Artefacts generated:")
        for row in generated:
            kind = str(row.get("kind") or "").strip()
            title = str(row.get("title") or "").strip()
            artefact_id = str(row.get("artefact_id") or "").strip()
            label = f"{kind}: {title}" if kind and title else (title or kind or "(unnamed)")
            if artefact_id:
                lines.append(f"- {label} (id {artefact_id})")
            else:
                lines.append(f"- {label}")

    return "\n".join(lines).strip() or raw


def _execute_prompt_guide() -> str:
    lines = [
        "EXECUTE needs an artefact request.",
        "",
        "Supported artefact kinds:",
        "- workbook",
        "- run_sheet",
        "- checklist",
        "- slides_outline",
        "- lesson_plan",
        "",
        "Use this prompt pattern:",
        "Propose exactly 3 artefacts in artefacts.proposed:",
        "1) kind=workbook, title=\"Session Workbook\", notes=\"Why: ...\\nStrawman: ...\\nTopics: ...\"",
        "2) kind=run_sheet, title=\"Facilitator Run Sheet\", notes=\"Why: ...\\nStrawman: ...\\nTopics: ...\"",
        "3) kind=checklist, title=\"Before/During/After Checklist\", notes=\"Why: ...\\nStrawman: ...\\nTopics: ...\"",
        "For each document include short seed content in notes with prefixes Why:/Strawman:/Topics:.",
        "Do not add any other kinds.",
    ]
    return "\n".join(lines)


def _guess_execute_kind(text: str) -> str:
    raw = str(text or "").strip().lower()
    if "lesson" in raw:
        return "lesson_plan"
    if "slide" in raw:
        return "slides_outline"
    if "run sheet" in raw or "run-sheet" in raw:
        return "run_sheet"
    if "checklist" in raw:
        return "checklist"
    if "workbook" in raw:
        return "workbook"
    return "workbook"


def _coerce_execute_payload_shape(payload: dict) -> dict:
    src = _as_dict(payload)
    out = empty_payload(WorkItem.PHASE_EXECUTE)
    out["meta"]["phase"] = WorkItem.PHASE_EXECUTE
    out["canonical_summary"] = str(src.get("canonical_summary") or "").strip()
    out["intent"] = _as_dict(src.get("intent")) if isinstance(src.get("intent"), dict) else out["intent"]
    out["explore"] = _as_dict(src.get("explore")) if isinstance(src.get("explore"), dict) else out["explore"]
    out["parked_for_later"] = _as_dict(src.get("parked_for_later")) if isinstance(src.get("parked_for_later"), dict) else out["parked_for_later"]
    out["validation"] = _as_dict(src.get("validation")) if isinstance(src.get("validation"), dict) else out["validation"]
    artefacts = _as_dict(src.get("artefacts"))
    proposed_rows = []
    for row in list(artefacts.get("proposed") or []):
        if isinstance(row, dict):
            kind = str(row.get("kind") or "").strip().lower()
            title = str(row.get("title") or "").strip()
            notes = str(row.get("notes") or "").strip()
            rationale = str(row.get("why") or row.get("rationale") or row.get("purpose") or "").strip()
            strawman = str(row.get("strawman") or row.get("seed") or row.get("seed_content") or "").strip()
            topics_raw = row.get("topics") or row.get("suggested_topics")
            topics = [str(v).strip() for v in list(topics_raw or []) if str(v).strip()]
            needed_inputs_raw = row.get("needed_inputs") or row.get("required_inputs") or row.get("requirements")
            needed_inputs = [str(v).strip() for v in list(needed_inputs_raw or []) if str(v).strip()]
            if rationale:
                notes = (notes + "\n" if notes else "") + f"Why: {rationale}"
            if strawman:
                notes = (notes + "\n" if notes else "") + f"Strawman: {strawman}"
            if topics:
                notes = (notes + "\n" if notes else "") + "Topics: " + "; ".join(topics)
            if needed_inputs:
                notes = (notes + "\n" if notes else "") + "Needs: " + "; ".join(needed_inputs)
            if not kind and title:
                kind = _guess_execute_kind(title)
            if not title:
                title = kind.replace("_", " ").title() if kind else "Proposed artefact"
            proposed_rows.append({"kind": kind or "workbook", "title": title, "notes": notes})
            continue
        text_row = str(row or "").strip()
        if not text_row:
            continue
        proposed_rows.append(
            {
                "kind": _guess_execute_kind(text_row),
                "title": text_row,
                "notes": "",
            }
        )
    if not proposed_rows:
        # Recovery path: if model only gave requirements-by-kind, derive proposed rows.
        req_map = _as_dict(artefacts.get("requirements"))
        for req_kind in req_map.keys():
            kind = str(req_kind or "").strip().lower()
            if not kind:
                continue
            proposed_rows.append(
                {
                    "kind": kind,
                    "title": kind.replace("_", " ").title(),
                    "notes": "",
                }
            )
    artefacts["proposed"] = proposed_rows
    # Never trust model-authored generated rows. Generated rows are system-produced only.
    artefacts["generated"] = []
    requirements_in = _as_dict(artefacts.get("requirements"))
    requirements_out = {}
    for req_kind, req_rows in requirements_in.items():
        kind = str(req_kind or "").strip()
        if not kind:
            continue
        rows = _as_list_of_str(req_rows)
        requirements_out[kind] = rows
    intake_in = _as_dict(artefacts.get("intake"))
    intake_out = {}
    for idx_key, intake_row in intake_in.items():
        key = str(idx_key or "").strip()
        if not key:
            continue
        row = _as_dict(intake_row)
        status = str(row.get("status") or "").strip().upper() or "MISSING_INPUTS"
        reqs = _as_list_of_str(row.get("requirements"))
        intake_out[key] = {"status": status, "requirements": reqs}
    artefacts["requirements"] = requirements_out
    artefacts["intake"] = intake_out
    out["artefacts"] = artefacts
    return out


def _as_dict(value: object) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _as_list_of_str(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _coerce_phase_payload_any(payload: dict, *, phase: str) -> dict:
    phase_upper = str(phase or "").strip().upper() or WorkItem.PHASE_DEFINE
    src = _as_dict(payload)

    # Unwrap common wrappers.
    for key in ("payload", "data", "response", "result", "output", "json"):
        inner = src.get(key)
        if isinstance(inner, dict):
            src = _as_dict(inner)
            break
        if isinstance(inner, str):
            parsed = _try_parse_json_payload(inner)
            if isinstance(parsed, dict):
                src = _as_dict(parsed)
                break

    out = empty_payload(phase_upper)
    out["meta"]["phase"] = phase_upper
    out["canonical_summary"] = str(src.get("canonical_summary") or src.get("headline") or "").strip()

    intent = _as_dict(src.get("intent"))
    explore = _as_dict(src.get("explore"))
    core = _as_dict(src.get("core"))

    out["intent"]["destination"] = str(
        intent.get("destination") or src.get("destination") or core.get("end_in_mind") or ""
    ).strip()
    out["intent"]["success_criteria"] = (
        _as_list_of_str(intent.get("success_criteria"))
        or _as_list_of_str(src.get("success_criteria"))
        or _as_list_of_str(core.get("destination_conditions"))
    )
    out["intent"]["constraints"] = (
        _as_list_of_str(intent.get("constraints"))
        or _as_list_of_str(src.get("constraints"))
        or _as_list_of_str(core.get("assumptions"))
    )
    out["intent"]["non_goals"] = (
        _as_list_of_str(intent.get("non_goals"))
        or _as_list_of_str(src.get("non_goals"))
        or _as_list_of_str(core.get("non_goals"))
    )
    out["intent"]["assumptions"] = (
        _as_list_of_str(intent.get("assumptions"))
        or _as_list_of_str(src.get("assumptions"))
        or _as_list_of_str(core.get("assumptions"))
    )
    out["intent"]["open_questions"] = (
        _as_list_of_str(intent.get("open_questions"))
        or _as_list_of_str(src.get("open_questions"))
        or _as_list_of_str(core.get("ambiguities"))
    )

    out["explore"]["adjacent_ideas"] = (
        _as_list_of_str(explore.get("adjacent_ideas"))
        or _as_list_of_str(src.get("adjacent_ideas"))
        or _as_list_of_str(core.get("adjacent_angles"))
    )
    out["explore"]["risks"] = (
        _as_list_of_str(explore.get("risks"))
        or _as_list_of_str(src.get("risks"))
        or _as_list_of_str(core.get("risks"))
    )
    out["explore"]["tradeoffs"] = (
        _as_list_of_str(explore.get("tradeoffs"))
        or _as_list_of_str(src.get("tradeoffs"))
        or _as_list_of_str(core.get("scope_changes"))
    )
    out["explore"]["reframes"] = (
        _as_list_of_str(explore.get("reframes"))
        or _as_list_of_str(src.get("reframes"))
        or _as_list_of_str(core.get("ambiguities"))
    )

    parked = _as_dict(src.get("parked_for_later"))
    parked_items = []
    for row in list(parked.get("items") or []):
        if isinstance(row, dict):
            title = str(row.get("title") or "").strip()
            detail = str(row.get("detail") or "").strip()
            if title or detail:
                parked_items.append({"title": title, "detail": detail})
        else:
            text = str(row or "").strip()
            if text:
                parked_items.append({"title": text, "detail": ""})
    for text in _as_list_of_str(src.get("parked")):
        parked_items.append({"title": text, "detail": ""})
    out["parked_for_later"]["items"] = parked_items

    artefacts = _as_dict(src.get("artefacts"))
    out["artefacts"]["proposed"] = list(artefacts.get("proposed") or [])
    out["artefacts"]["generated"] = list(artefacts.get("generated") or [])
    return out


def _phase_payload_recovered(payload: dict, *, phase: str) -> tuple[bool, dict, str]:
    coerced = _coerce_phase_payload_any(payload, phase=phase)
    ok_schema, schema_errors = validate_structural(coerced)
    ok_phase, phase_errors = check_required_nonempty(coerced, phase=phase)
    if ok_schema and ok_phase:
        return True, coerced, ""
    error_text = "; ".join(
        [
            str(e)
            for e in list(schema_errors or []) + list(phase_errors or [])
            if str(e).strip()
        ]
    ).strip()
    return False, coerced, error_text or "Recovered payload still invalid."


def _backfill_refine_from_explore(*, refine_payload: dict, explore_payload: dict) -> dict:
    out = _as_dict(refine_payload)
    intent = _as_dict(out.get("intent"))
    explore = _as_dict(out.get("explore"))
    src_intent = _as_dict(_as_dict(explore_payload).get("intent"))
    src_explore = _as_dict(_as_dict(explore_payload).get("explore"))

    def _pick_list(current: object, fallback: object) -> list[str]:
        cur = _as_list_of_str(current)
        if cur:
            return cur
        return _as_list_of_str(fallback)

    destination = str(intent.get("destination") or "").strip() or str(src_intent.get("destination") or "").strip()
    intent["destination"] = destination
    intent["success_criteria"] = _pick_list(intent.get("success_criteria"), src_intent.get("success_criteria"))
    intent["constraints"] = _pick_list(intent.get("constraints"), src_intent.get("constraints"))
    intent["non_goals"] = _pick_list(intent.get("non_goals"), src_intent.get("non_goals"))
    intent["assumptions"] = _pick_list(intent.get("assumptions"), src_intent.get("assumptions"))
    intent["open_questions"] = _pick_list(intent.get("open_questions"), src_intent.get("open_questions"))

    explore["adjacent_ideas"] = _pick_list(explore.get("adjacent_ideas"), src_explore.get("adjacent_ideas"))
    explore["risks"] = _pick_list(explore.get("risks"), src_explore.get("risks"))
    explore["tradeoffs"] = _pick_list(explore.get("tradeoffs"), src_explore.get("tradeoffs"))
    explore["reframes"] = _pick_list(explore.get("reframes"), src_explore.get("reframes"))

    out["intent"] = intent
    out["explore"] = explore
    return out


def _backfill_approve_from_refine(*, approve_payload: dict, refine_payload: dict) -> dict:
    out = _as_dict(approve_payload)
    src = _as_dict(refine_payload)
    intent = _as_dict(out.get("intent"))
    explore = _as_dict(out.get("explore"))
    src_intent = _as_dict(src.get("intent"))
    src_explore = _as_dict(src.get("explore"))

    def _pick_list(current: object, fallback: object) -> list[str]:
        cur = _as_list_of_str(current)
        if cur:
            return cur
        return _as_list_of_str(fallback)

    if not str(out.get("canonical_summary") or "").strip():
        summary = str(src.get("canonical_summary") or "").strip()
        if not summary:
            summary = str(src_intent.get("destination") or "").strip()
        out["canonical_summary"] = summary[:240]

    intent["destination"] = str(intent.get("destination") or "").strip() or str(src_intent.get("destination") or "").strip()
    intent["success_criteria"] = _pick_list(intent.get("success_criteria"), src_intent.get("success_criteria"))
    intent["constraints"] = _pick_list(intent.get("constraints"), src_intent.get("constraints"))
    intent["non_goals"] = _pick_list(intent.get("non_goals"), src_intent.get("non_goals"))
    intent["assumptions"] = _pick_list(intent.get("assumptions"), src_intent.get("assumptions"))
    intent["open_questions"] = _pick_list(intent.get("open_questions"), src_intent.get("open_questions"))

    explore["adjacent_ideas"] = _pick_list(explore.get("adjacent_ideas"), src_explore.get("adjacent_ideas"))
    explore["risks"] = _pick_list(explore.get("risks"), src_explore.get("risks"))
    explore["tradeoffs"] = _pick_list(explore.get("tradeoffs"), src_explore.get("tradeoffs"))
    explore["reframes"] = _pick_list(explore.get("reframes"), src_explore.get("reframes"))

    out["intent"] = intent
    out["explore"] = explore
    return out


def _sanitise_execute_payload(payload: dict) -> dict:
    out = _as_dict(payload)
    out.setdefault("meta", {})
    out["meta"]["phase"] = WorkItem.PHASE_EXECUTE

    intent_in = _as_dict(out.get("intent"))
    out["intent"] = {
        "destination": "",
        "success_criteria": [],
        "constraints": [],
        "non_goals": [],
        "assumptions": [],
        "open_questions": [str(v).strip() for v in list(intent_in.get("open_questions") or []) if str(v).strip()][:3],
    }
    out["explore"] = {
        "adjacent_ideas": [],
        "risks": [],
        "tradeoffs": [],
        "reframes": [],
    }
    artefacts_in = _as_dict(out.get("artefacts"))
    proposed = list(artefacts_in.get("proposed") or [])
    generated = list(artefacts_in.get("generated") or [])
    requirements_in = _as_dict(artefacts_in.get("requirements"))
    requirements = {}
    for req_kind, req_rows in requirements_in.items():
        key = str(req_kind or "").strip()
        if not key:
            continue
        rows = _as_list_of_str(req_rows)
        requirements[key] = rows
    intake_in = _as_dict(artefacts_in.get("intake"))
    intake = {}
    for intake_key, intake_row in intake_in.items():
        idx_key = str(intake_key or "").strip()
        if not idx_key:
            continue
        row = _as_dict(intake_row)
        status = str(row.get("status") or "").strip().upper() or "MISSING_INPUTS"
        reqs = _as_list_of_str(row.get("requirements"))
        intake[idx_key] = {"status": status, "requirements": reqs}
    out["artefacts"] = {
        "proposed": proposed,
        "generated": generated,
        "requirements": requirements,
        "intake": intake,
    }
    return out


def _execute_requirements_map(payload: dict) -> dict:
    artefacts = _as_dict(_as_dict(payload).get("artefacts"))
    requirements = _as_dict(artefacts.get("requirements"))
    out = {}
    for req_kind, req_rows in requirements.items():
        key = str(req_kind or "").strip()
        if not key:
            continue
        rows = _as_list_of_str(req_rows)
        out[key] = rows
    return out


def _execute_has_requirements(payload: dict) -> bool:
    return bool(_execute_requirements_map(payload))


def _execute_proposed_kinds(payload: dict) -> list[str]:
    artefacts = _as_dict(_as_dict(payload).get("artefacts"))
    out = []
    for row in list(artefacts.get("proposed") or []):
        row_dict = _as_dict(row)
        kind = str(row_dict.get("kind") or "").strip()
        if kind and kind not in out:
            out.append(kind)
    return out


def _execute_has_missing_proposed_kind(payload: dict) -> bool:
    artefacts = _as_dict(_as_dict(payload).get("artefacts"))
    for row in list(artefacts.get("proposed") or []):
        row_dict = _as_dict(row)
        kind = str(row_dict.get("kind") or "").strip()
        title = str(row_dict.get("title") or "").strip()
        notes = str(row_dict.get("notes") or "").strip()
        if (title or notes) and not kind:
            return True
    return False


def _execute_missing_requirement_kinds(payload: dict) -> list[str]:
    if _execute_has_missing_proposed_kind(payload):
        return ["unspecified_kind"]
    kinds = _execute_proposed_kinds(payload)
    reqs = _execute_requirements_map(payload)
    missing = []
    for kind in kinds:
        if kind not in reqs:
            missing.append(kind)
    return missing


def _execute_unresolved_requirement_kinds(payload: dict) -> list[str]:
    if _execute_has_missing_proposed_kind(payload):
        return ["unspecified_kind"]
    kinds = _execute_proposed_kinds(payload)
    reqs = _execute_requirements_map(payload)
    unresolved = []
    for kind in kinds:
        if len(list(reqs.get(kind) or [])) > 0:
            unresolved.append(kind)
    return unresolved


def _parse_execute_doc_inputs(raw_text: str) -> dict[int, str]:
    raw = str(raw_text or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[int, str] = {}
    for key, value in parsed.items():
        try:
            idx = int(str(key or "").strip())
        except Exception:
            continue
        text = str(value or "").strip()
        if idx >= 0 and text:
            out[idx] = text
    return out


def _execute_doc_why_text(row: dict) -> str:
    notes = str(_as_dict(row).get("notes") or "").strip()
    if not notes:
        return ""
    lines = [str(x).strip() for x in notes.splitlines() if str(x).strip()]
    for line in lines:
        lower = line.lower()
        if lower.startswith("why:"):
            return line.split(":", 1)[1].strip()
    return lines[0] if lines else ""


def _execute_doc_strawman_text(row: dict) -> str:
    notes = str(_as_dict(row).get("notes") or "").strip()
    if not notes:
        return ""
    lines = [str(x).strip() for x in notes.splitlines() if str(x).strip()]
    for line in lines:
        lower = line.lower()
        if lower.startswith("strawman:") or lower.startswith("seed:"):
            return line.split(":", 1)[1].strip()
    return ""


def _execute_doc_topics(row: dict) -> list[str]:
    notes = str(_as_dict(row).get("notes") or "").strip()
    if not notes:
        return []
    lines = [str(x).strip() for x in notes.splitlines() if str(x).strip()]
    for line in lines:
        lower = line.lower()
        if lower.startswith("topics:") or lower.startswith("suggested topics:"):
            raw = line.split(":", 1)[1].strip()
            parts = [p.strip(" -") for p in re.split(r"[;,]", raw) if p.strip(" -")]
            return parts
    return []


def _execute_intake_map(payload: dict) -> dict[str, dict]:
    artefacts = _as_dict(_as_dict(payload).get("artefacts"))
    intake = _as_dict(artefacts.get("intake"))
    out = {}
    for key, value in intake.items():
        idx_key = str(key or "").strip()
        if not idx_key:
            continue
        row = _as_dict(value)
        status = str(row.get("status") or "").strip().upper() or "MISSING_INPUTS"
        reqs = _as_list_of_str(row.get("requirements"))
        out[idx_key] = {"status": status, "requirements": reqs}
    return out


def _refresh_execute_intake(payload: dict) -> dict:
    out = _sanitise_execute_payload(payload)
    artefacts = _as_dict(out.get("artefacts"))
    proposed = list(artefacts.get("proposed") or [])
    req_by_kind = _execute_requirements_map(out)
    intake = {}
    for idx, row in enumerate(proposed):
        row_dict = _as_dict(row)
        kind = str(row_dict.get("kind") or "").strip()
        title = str(row_dict.get("title") or "").strip()
        kind_has_decision = bool(kind and (kind in req_by_kind))
        reqs = list(req_by_kind.get(kind) or []) if kind_has_decision else []
        if not kind:
            reqs = ["Set document kind"]
        if not title:
            reqs = list(reqs) + ["Set document title"]
        if kind and title and not kind_has_decision:
            reqs = list(reqs) + ["Intake has not listed required inputs for this document yet"]
        status = "READY" if (kind and title and kind_has_decision and not reqs) else "MISSING_INPUTS"
        intake[str(idx)] = {"status": status, "requirements": reqs}
    artefacts["intake"] = intake
    out["artefacts"] = artefacts
    return out


def _sanitise_define_payload(payload: dict) -> dict:
    out = _as_dict(payload)
    out.setdefault("meta", {})
    out["meta"]["phase"] = WorkItem.PHASE_DEFINE
    out.setdefault("explore", {})
    out["explore"] = {
        "adjacent_ideas": [],
        "risks": [],
        "tradeoffs": [],
        "reframes": [],
    }
    return out


def _latest_seed_by_reason(work_item: WorkItem, reason_text: str) -> str:
    expected = str(reason_text or "").strip().upper()
    for row in reversed(list(work_item.seed_log or [])):
        if not isinstance(row, dict):
            continue
        reason = str(row.get("reason") or "").strip().upper()
        if reason != expected:
            continue
        text = str(row.get("seed_text") or "").strip()
        if text:
            return text
    return ""


def _raw_phase_contract_text(phase_name: str) -> str:
    phase = str(phase_name or "").strip().upper()
    if phase in DERAX_PHASES:
        return build_phase_contract_text(phase).strip()
    return ""


def _phase_contract_key(phase_name: str) -> str:
    phase = str(phase_name or "").strip().upper()
    return f"phase.{phase.lower()}"


def _phase_contract_default_text(phase_name: str) -> str:
    phase = str(phase_name or "").strip().upper()
    return _raw_phase_contract_text(phase)


def _phase_contract_effective_text(*, user, phase_name: str, project_id: int | None = None) -> tuple[str, str]:
    contract_key = _phase_contract_key(phase_name)
    resolved = resolve_contract_text(user, contract_key, project_id=project_id)
    default_text = _phase_contract_default_text(phase_name)
    effective_text = str(resolved.get("effective_text") or "").strip()
    source = str(resolved.get("effective_source") or "DEFAULT").strip().upper()
    if not effective_text:
        effective_text = default_text
        source = "DEFAULT"
    return effective_text, source


def _parse_history_payload(text: str):
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if all(k in payload for k in ("meta", "intent", "explore", "parked_for_later")):
        return payload
    if all(k in payload for k in ("phase", "core", "parked", "footnotes", "next", "meta")):
        return payload
    return None


def _safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    cleaned = cleaned.strip("._-")
    return cleaned or "DERAX"


def _display_file_name(name: str) -> str:
    raw = str(name or "").strip()
    base = raw.rsplit("/", 1)[-1]
    if "__" in base:
        parts = [p for p in base.split("__") if p]
        if len(parts) >= 3:
            base = "__".join(parts[1:])
    if "." in base:
        stem, ext = base.rsplit(".", 1)
    else:
        stem, ext = base, ""
    dup = re.match(r"^([A-Za-z0-9-]+)_[A-Za-z0-9-]+", stem)
    if dup:
        token = str(dup.group(1) or "")
        if stem.lower().startswith((token + "_" + token + "_").lower()):
            stem = stem[len(token) + 1:]
    base = (stem + ("." + ext if ext else "")).strip()
    if "_" in base:
        tail = base.split("_", 1)[1].lstrip("_").strip()
        if tail:
            return tail
    return base or raw


def _list_from_payload(payload: dict, *paths: tuple[str, str]) -> list[str]:
    for path in paths:
        node = payload
        ok = True
        for part in path:
            if not isinstance(node, dict):
                ok = False
                break
            node = node.get(part)
        if not ok:
            continue
        if isinstance(node, list):
            return [str(v).strip() for v in node if str(v).strip()]
    return []


def _list_to_multiline(values: list[str]) -> str:
    return "\n".join([str(v).strip() for v in list(values or []) if str(v).strip()])


def _multiline_to_list(text: str) -> list[str]:
    out = []
    for raw in str(text or "").splitlines():
        value = str(raw or "").strip()
        if value.startswith("- "):
            value = value[2:].strip()
        if value:
            out.append(value)
    return out


def _multiline_to_execute_proposed(text: str) -> list[dict]:
    rows = []
    for raw in str(text or "").splitlines():
        line = str(raw or "").strip()
        if not line:
            continue
        parts = [str(p or "").strip() for p in line.split("|")]
        if len(parts) >= 3:
            kind = parts[0].lower()
            title = parts[1]
            notes = " | ".join(parts[2:])
        elif len(parts) == 2:
            kind = parts[0].lower()
            title = parts[1]
            notes = ""
        else:
            kind = _guess_execute_kind(line)
            title = line
            notes = ""
        if not title:
            title = kind.replace("_", " ").title() if kind else "Proposed artefact"
        if not kind:
            kind = _guess_execute_kind(title)
        rows.append({"kind": kind, "title": title, "notes": notes})
    return rows


def _execute_proposed_to_multiline(rows: list) -> str:
    out = []
    for row in list(rows or []):
        if isinstance(row, dict):
            kind = str(row.get("kind") or "").strip()
            title = str(row.get("title") or "").strip()
            notes = str(row.get("notes") or "").strip()
            if kind and title and notes:
                out.append(f"{kind} | {title} | {notes}")
            elif kind and title:
                out.append(f"{kind} | {title}")
            elif title:
                out.append(title)
        else:
            text = str(row or "").strip()
            if text:
                out.append(text)
    return "\n".join(out)


def _str_from_payload(payload: dict, *paths: tuple[str, str]) -> str:
    for path in paths:
        node = payload
        ok = True
        for part in path:
            if not isinstance(node, dict):
                ok = False
                break
            node = node.get(part)
        if not ok:
            continue
        if isinstance(node, str) and node.strip():
            return node.strip()
    return ""


def _latest_payload_for_phase(work_item: WorkItem, phase: str) -> dict:
    phase_upper = str(phase or "").strip().upper()
    if phase_upper == WorkItem.PHASE_DEFINE:
        history = list(work_item.derax_define_history or [])
    elif phase_upper == WorkItem.PHASE_EXPLORE:
        history = list(work_item.derax_explore_history or [])
    else:
        return _latest_payload_from_runs(work_item, phase_upper)
    for row in reversed(history):
        if not isinstance(row, dict):
            continue
        if str(row.get("role") or "").strip().lower() != "assistant":
            continue
        parsed = _parse_history_payload(str(row.get("text") or ""))
        if isinstance(parsed, dict):
            return parsed
    return {}


def _latest_payload_from_runs(work_item: WorkItem, phase: str) -> dict:
    phase_upper = str(phase or "").strip().upper()
    runs = list(getattr(work_item, "derax_runs", []) or [])
    for run in reversed(runs):
        if not isinstance(run, dict):
            continue
        if str(run.get("phase") or "").strip().upper() != phase_upper:
            continue
        asset_id = run.get("asset_id")
        try:
            asset_id_int = int(asset_id)
        except (TypeError, ValueError):
            continue
        doc = ProjectDocument.objects.filter(id=asset_id_int, project=work_item.project).first()
        if doc is None:
            continue
        try:
            doc.file.open("rb")
            try:
                raw = doc.file.read()
            finally:
                doc.file.close()
            parsed = json.loads(raw.decode("utf-8", errors="ignore"))
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    return {}


def _latest_refine_response_text(work_item: WorkItem) -> str:
    payload = _latest_payload_from_runs(work_item, WorkItem.PHASE_REFINE)
    if payload:
        return _readable_derax_text(json.dumps(payload, ensure_ascii=True, indent=2))
    return ""


def _explore_view_model(payload: dict) -> dict:
    p = payload if isinstance(payload, dict) else {}
    destination = _str_from_payload(p, ("intent", "destination"), ("core", "end_in_mind"))
    adjacent_ideas = _list_from_payload(p, ("explore", "adjacent_ideas"), ("core", "adjacent_angles"))
    risks = _list_from_payload(p, ("explore", "risks"), ("core", "risks"))
    tradeoffs = _list_from_payload(p, ("explore", "tradeoffs"), ("core", "scope_changes"))
    reframes = _list_from_payload(p, ("explore", "reframes"), ("core", "ambiguities"))
    has_structured = bool(destination or adjacent_ideas or risks or tradeoffs or reframes)
    return {
        "destination": destination,
        "adjacent_ideas": adjacent_ideas,
        "risks": risks,
        "tradeoffs": tradeoffs,
        "reframes": reframes,
        "has_structured": has_structured,
    }


def _refine_view_model(payload: dict) -> dict:
    p = payload if isinstance(payload, dict) else {}
    destination = _str_from_payload(p, ("intent", "destination"), ("core", "end_in_mind"))
    success_criteria = _list_from_payload(p, ("intent", "success_criteria"), ("core", "destination_conditions"))
    constraints = _list_from_payload(p, ("intent", "constraints"), ("core", "assumptions"))
    non_goals = _list_from_payload(p, ("intent", "non_goals"), ("core", "non_goals"))
    assumptions = _list_from_payload(p, ("intent", "assumptions"), ("core", "assumptions"))
    open_questions = _list_from_payload(p, ("intent", "open_questions"), ("core", "ambiguities"))
    adjacent_ideas = _list_from_payload(p, ("explore", "adjacent_ideas"), ("core", "adjacent_angles"))
    risks = _list_from_payload(p, ("explore", "risks"), ("core", "risks"))
    tradeoffs = _list_from_payload(p, ("explore", "tradeoffs"), ("core", "scope_changes"))
    reframes = _list_from_payload(p, ("explore", "reframes"), ("core", "ambiguities"))
    parked_items = []
    for item in list((p.get("parked_for_later") or {}).get("items") or []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        detail = str(item.get("detail") or "").strip()
        if title and detail:
            parked_items.append(f"{title}: {detail}")
        elif title:
            parked_items.append(title)
        elif detail:
            parked_items.append(detail)
    has_structured = bool(
        destination
        or success_criteria
        or constraints
        or non_goals
        or assumptions
        or open_questions
        or adjacent_ideas
        or risks
        or tradeoffs
        or reframes
        or parked_items
    )
    return {
        "destination": destination,
        "success_criteria": success_criteria,
        "constraints": constraints,
        "non_goals": non_goals,
        "assumptions": assumptions,
        "open_questions": open_questions,
        "adjacent_ideas": adjacent_ideas,
        "risks": risks,
        "tradeoffs": tradeoffs,
        "reframes": reframes,
        "parked_items": parked_items,
        "has_structured": has_structured,
    }


def _build_editable_markdown(payload: dict, *, phase: str) -> str:
    phase_upper = str(phase or "").strip().upper() or WorkItem.PHASE_DEFINE
    destination = _str_from_payload(payload, ("intent", "destination"), ("core", "end_in_mind"))
    success_criteria = _list_from_payload(payload, ("intent", "success_criteria"), ("core", "destination_conditions"))
    constraints = _list_from_payload(payload, ("intent", "constraints"), ("core", "assumptions"))
    non_goals = _list_from_payload(payload, ("intent", "non_goals"), ("core", "non_goals"))
    open_questions = _list_from_payload(payload, ("intent", "open_questions"), ("core", "ambiguities"))
    adjacent = _list_from_payload(payload, ("explore", "adjacent_ideas"), ("core", "adjacent_angles"))
    risks = _list_from_payload(payload, ("explore", "risks"), ("core", "risks"))
    tradeoffs = _list_from_payload(payload, ("explore", "tradeoffs"), ("core", "scope_changes"))
    reframes = _list_from_payload(payload, ("explore", "reframes"), ("core", "ambiguities"))

    parked_items = []
    for item in list((payload.get("parked_for_later") or {}).get("items") or []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        detail = str(item.get("detail") or "").strip()
        if title and detail:
            parked_items.append(f"{title}: {detail}")
        elif title:
            parked_items.append(title)
        elif detail:
            parked_items.append(detail)
    if not parked_items:
        parked_items = [str(v).strip() for v in list(payload.get("parked") or []) if str(v).strip()]

    lines = [
        "# DERAX Editable Draft",
        "",
        "## Phase",
        "",
        phase_upper,
        "",
        "## End in mind",
        "",
        destination,
        "",
        "## Success criteria",
        "",
    ]
    lines.extend([f"- {v}" for v in success_criteria] or ["- "])
    lines.extend(
        [
            "",
            "## Constraints",
            "",
        ]
    )
    lines.extend([f"- {v}" for v in constraints] or ["- "])
    lines.extend(["", "## Non-goals", ""])
    lines.extend([f"- {v}" for v in non_goals] or ["- "])
    lines.extend(["", "## Open questions", ""])
    lines.extend([f"- {v}" for v in open_questions] or ["- "])
    lines.extend(["", "## Adjacent ideas", ""])
    lines.extend([f"- {v}" for v in adjacent] or ["- "])
    lines.extend(["", "## Risks", ""])
    lines.extend([f"- {v}" for v in risks] or ["- "])
    lines.extend(["", "## Trade-offs", ""])
    lines.extend([f"- {v}" for v in tradeoffs] or ["- "])
    lines.extend(["", "## Reframes", ""])
    lines.extend([f"- {v}" for v in reframes] or ["- "])
    lines.extend(["", "## Parked for later", ""])
    lines.extend([f"- {v}" for v in parked_items] or ["- "])
    lines.append("")
    return "\n".join(lines)


def _build_refine_input_draft(*, refine_input: str, explore_input: str, contract_text: str) -> str:
    lines = [
        "# DERAX Refine Input Draft",
        "",
        "## Explore output (starting point)",
        "",
        str(explore_input or "").strip(),
        "",
        "## Refine working input",
        "",
        str(refine_input or "").strip(),
        "",
        "## Refine contract",
        "",
        str(contract_text or "").strip(),
        "",
    ]
    return "\n".join(lines)


def _parse_editable_markdown(markdown_text: str) -> dict:
    sections = _parse_markdown_sections(markdown_text)

    def section_text(name: str) -> str:
        rows = sections.get(name.lower(), [])
        out = [r.strip() for r in rows if r.strip() and not r.strip().startswith("- ")]
        return " ".join(out).strip()

    def section_bullets(name: str) -> list[str]:
        rows = sections.get(name.lower(), [])
        out = []
        for row in rows:
            value = row.strip()
            if value.startswith("- "):
                value = value[2:].strip()
            if value:
                out.append(value)
        return out

    phase = section_text("Phase").upper()
    payload = empty_payload(phase if phase in DERAX_PHASES else WorkItem.PHASE_DEFINE)
    payload["meta"]["phase"] = phase if phase in DERAX_PHASES else WorkItem.PHASE_DEFINE
    payload["meta"]["derax_version"] = "1.0"
    payload["meta"]["timestamp"] = timezone.now().isoformat()
    payload["intent"]["destination"] = section_text("End in mind")
    payload["intent"]["success_criteria"] = section_bullets("Success criteria")
    payload["intent"]["constraints"] = section_bullets("Constraints")
    payload["intent"]["non_goals"] = section_bullets("Non-goals")
    payload["intent"]["open_questions"] = section_bullets("Open questions")
    payload["explore"]["adjacent_ideas"] = section_bullets("Adjacent ideas")
    payload["explore"]["risks"] = section_bullets("Risks")
    payload["explore"]["tradeoffs"] = section_bullets("Trade-offs")
    payload["explore"]["reframes"] = section_bullets("Reframes")
    payload["parked_for_later"]["items"] = [{"title": item, "detail": ""} for item in section_bullets("Parked for later")]
    return payload


def _parse_markdown_sections(markdown_text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = ""
    for raw_line in str(markdown_text or "").splitlines():
        line = str(raw_line or "").rstrip("\n")
        if line.startswith("## "):
            current = line[3:].strip().lower()
            sections[current] = []
            continue
        if current:
            sections[current].append(line)
    return sections


def _extract_refine_input_from_markdown(markdown_text: str) -> str:
    sections = _parse_markdown_sections(markdown_text)
    rows = sections.get("refine working input", [])
    if not rows:
        rows = sections.get("refine input", [])
    out = [str(r).rstrip() for r in rows if str(r).strip()]
    return "\n".join(out).strip()


def _try_parse_json_payload(raw_text: str):
    text = str(raw_text or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
        if isinstance(payload, dict):
            return payload
    except Exception:
        return None
    return None


def _payload_shape_debug(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    _ok, parsed, _errors = validate_derax_text(text)
    if not isinstance(parsed, dict):
        parsed = _try_parse_json_payload(text)
    if not isinstance(parsed, dict):
        return "shape=unparsed"
    top_keys = sorted([str(k) for k in parsed.keys()])
    wrapped_keys = []
    for key in ("payload", "data", "response", "result", "output", "json"):
        inner = parsed.get(key)
        if isinstance(inner, dict):
            wrapped_keys.append(f"{key}:{','.join(sorted([str(k) for k in inner.keys()]))}")
    if wrapped_keys:
        return f"shape=parsed; keys={','.join(top_keys)}; wrapped={';'.join(wrapped_keys)}"
    return f"shape=parsed; keys={','.join(top_keys)}"


def _payload_candidate_from_text(raw_text: str):
    text = str(raw_text or "").strip()
    _ok, parsed, _errors = validate_derax_text(text)
    if isinstance(parsed, dict):
        return parsed
    parsed = _try_parse_json_payload(text)
    if isinstance(parsed, dict):
        return parsed
    return None


def _extract_text_from_odt_bytes(raw_bytes: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            xml_bytes = zf.read("content.xml")
    except Exception:
        return ""
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return ""

    lines = []
    for elem in root.iter():
        tag = str(elem.tag or "")
        if "}" in tag:
            tag = tag.split("}", 1)[1]
        text = " ".join(" ".join((elem.itertext() or [])).split()).strip()
        if not text:
            continue
        if tag == "h":
            lines.append(f"## {text}")
        elif tag == "p":
            lines.append(text)
    return "\n".join(lines).strip()


def _odt_bytes_from_text(text: str) -> bytes:
    body = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = body.split("\n")
    content_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
        'office:version="1.2">',
        "<office:body><office:text>",
    ]
    for raw in lines:
        line = str(raw or "")
        escaped = (
            line.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        stripped = escaped.strip()
        if not stripped:
            content_lines.append("<text:p/>")
            continue
        if stripped.startswith("# "):
            heading_text = stripped[2:].strip() or "Untitled"
            content_lines.append(f'<text:h text:outline-level="1">{heading_text}</text:h>')
            continue
        if stripped.startswith("## "):
            heading_text = stripped[3:].strip() or "Untitled"
            content_lines.append(f'<text:h text:outline-level="2">{heading_text}</text:h>')
            continue
        if stripped.startswith("- "):
            bullet_text = stripped[2:].strip()
            content_lines.append("<text:list><text:list-item>")
            content_lines.append(f"<text:p>{bullet_text}</text:p>")
            content_lines.append("</text:list-item></text:list>")
            continue
        content_lines.append(f"<text:p>{escaped}</text:p>")
    content_lines.extend(["</office:text></office:body>", "</office:document-content>"])
    content_xml = "\n".join(content_lines).encode("utf-8")

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-styles xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'office:version="1.2"><office:styles/></office:document-styles>'
    ).encode("utf-8")
    meta_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'office:version="1.2"><office:meta/></office:document-meta>'
    ).encode("utf-8")
    manifest_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
        'manifest:version="1.2">'
        '<manifest:file-entry manifest:full-path="/" '
        'manifest:media-type="application/vnd.oasis.opendocument.text"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>'
        '</manifest:manifest>'
    ).encode("utf-8")

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zf:
        # ODT requires mimetype first and uncompressed.
        zf.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/vnd.oasis.opendocument.text",
            compress_type=zipfile.ZIP_STORED,
        )
        zf.writestr("content.xml", content_xml, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("styles.xml", styles_xml, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("meta.xml", meta_xml, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("META-INF/manifest.xml", manifest_xml, compress_type=zipfile.ZIP_DEFLATED)
    return output.getvalue()


def _extract_document_text(doc: ProjectDocument, raw_bytes: bytes) -> str:
    name = str(getattr(doc, "original_name", "") or getattr(doc, "title", "")).strip().lower()
    if name.endswith(".odt"):
        odt_text = _extract_text_from_odt_bytes(raw_bytes)
        if odt_text:
            return odt_text
    return raw_bytes.decode("utf-8", errors="ignore")


def _build_phase_history_rows(rows: list[dict]) -> list[dict]:
    built = []
    for row in reversed(list(rows or [])):
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "")
        payload = _parse_history_payload(text)
        built.append(
            {
                "role": str(row.get("role") or "").strip().lower(),
                "timestamp": str(row.get("timestamp") or ""),
                "text": text,
                "display_text": _readable_derax_text(text),
                "is_derax": payload is not None,
                "derax_payload": payload or {},
                "raw_json": json.dumps(payload, ensure_ascii=True, indent=2) if isinstance(payload, dict) else "",
            }
        )
    return built


def _merged_derax_audit_history(work_item: WorkItem) -> list[dict]:
    rows = []
    for item in list(work_item.derax_define_history or []):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "phase": WorkItem.PHASE_DEFINE,
                "role": str(item.get("role") or "").strip().lower(),
                "text": str(item.get("text") or ""),
                "display_text": _readable_derax_text(str(item.get("text") or "")),
                "timestamp": str(item.get("timestamp") or ""),
            }
        )
    for item in list(work_item.derax_explore_history or []):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "phase": WorkItem.PHASE_EXPLORE,
                "role": str(item.get("role") or "").strip().lower(),
                "text": str(item.get("text") or ""),
                "display_text": _readable_derax_text(str(item.get("text") or "")),
                "timestamp": str(item.get("timestamp") or ""),
            }
        )
    rows.sort(key=lambda row: str(row.get("timestamp") or ""))
    return rows


def _phase_route_map(work_item: WorkItem) -> list[dict]:
    active = str(work_item.active_phase or "").strip().upper() or WorkItem.PHASE_DEFINE
    define_locked = False
    explore_locked = False
    refine_locked = False
    approve_locked = False
    execute_locked = False
    for item in list(work_item.seed_log or []):
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or "").strip().upper()
        if reason == "DEFINE_LOCKED":
            define_locked = True
        if reason == "EXPLORE_LOCKED":
            explore_locked = True
        if reason == "REFINE_LOCKED":
            refine_locked = True
        if reason == "APPROVE_LOCKED":
            approve_locked = True
        if reason == "EXECUTE_LOCKED":
            execute_locked = True

    def status_for(phase: str) -> str:
        if phase == WorkItem.PHASE_DEFINE:
            if active == WorkItem.PHASE_DEFINE:
                return "ACTIVE"
            if define_locked:
                return "LOCKED"
            return "PROPOSED"
        if phase == WorkItem.PHASE_EXPLORE:
            if active == WorkItem.PHASE_EXPLORE:
                return "ACTIVE"
            if explore_locked:
                return "LOCKED"
            if define_locked:
                return "PROPOSED"
            return "PENDING"
        if phase == WorkItem.PHASE_REFINE:
            if active == WorkItem.PHASE_REFINE:
                return "ACTIVE"
            if refine_locked:
                return "LOCKED"
            if explore_locked:
                return "PROPOSED"
            return "PENDING"
        if phase == WorkItem.PHASE_APPROVE:
            if active == WorkItem.PHASE_APPROVE:
                return "ACTIVE"
            if approve_locked:
                return "LOCKED"
            if refine_locked:
                return "PROPOSED"
            return "PENDING"
        if phase == WorkItem.PHASE_EXECUTE:
            if execute_locked:
                return "LOCKED"
            if active == WorkItem.PHASE_EXECUTE:
                return "ACTIVE"
            if approve_locked:
                return "PROPOSED"
            return "PENDING"
        return "PENDING"

    return [
        {"phase": WorkItem.PHASE_DEFINE, "status": status_for(WorkItem.PHASE_DEFINE)},
        {"phase": WorkItem.PHASE_EXPLORE, "status": status_for(WorkItem.PHASE_EXPLORE)},
        {"phase": WorkItem.PHASE_REFINE, "status": status_for(WorkItem.PHASE_REFINE)},
        {"phase": WorkItem.PHASE_APPROVE, "status": status_for(WorkItem.PHASE_APPROVE)},
        {"phase": WorkItem.PHASE_EXECUTE, "status": status_for(WorkItem.PHASE_EXECUTE)},
    ]


def _phase_status_map(work_item: WorkItem) -> dict[str, str]:
    return {str(row.get("phase") or ""): str(row.get("status") or "") for row in _phase_route_map(work_item)}


def _build_refine_history_rows(work_item: WorkItem) -> list[dict]:
    return _build_run_history_rows(work_item, WorkItem.PHASE_REFINE)


def _build_run_history_rows(work_item: WorkItem, phase: str) -> list[dict]:
    phase_upper = str(phase or "").strip().upper()
    rows = []
    all_runs = list(getattr(work_item, "derax_runs", []) or [])
    for run_idx, run in enumerate(all_runs):
        if not isinstance(run, dict):
            continue
        if str(run.get("phase") or "").strip().upper() != phase_upper:
            continue
        asset_id = run.get("asset_id")
        try:
            asset_id_int = int(asset_id)
        except (TypeError, ValueError):
            continue
        doc = ProjectDocument.objects.filter(id=asset_id_int, project=work_item.project).first()
        if doc is None:
            continue
        try:
            doc.file.open("rb")
            try:
                raw = doc.file.read()
            finally:
                doc.file.close()
            payload = json.loads(raw.decode("utf-8", errors="ignore"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        text = _readable_derax_text(json.dumps(payload, ensure_ascii=True, indent=2))
        rows.append(
            {
                "role": "assistant",
                "timestamp": str(run.get("created_at") or ""),
                "text": text,
                "display_text": text,
                "is_derax": True,
                "derax_payload": payload,
                "raw_json": json.dumps(payload, ensure_ascii=True, indent=2),
                "_run_idx": run_idx,
            }
        )
    rows.sort(key=lambda row: int(row.get("_run_idx") or -1), reverse=True)
    for row in rows:
        row.pop("_run_idx", None)
    return rows


@login_required
def derax_project_home(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), id=project_id)
    work_item = _primary_work_item_for_project(project)
    can_edit_phase_contracts = bool(
        request.user.id == project.owner_id or request.user.is_staff or request.user.is_superuser
    )
    can_archive_project_docs = bool(
        request.user.id == project.owner_id or request.user.is_staff or request.user.is_superuser
    )
    can_hard_delete_project_docs = bool(request.user.is_staff or request.user.is_superuser)

    if request.method == "POST":
        action = str(request.POST.get("action") or "").strip().lower()
        if action == "save_refine_structured":
            existing = _latest_payload_from_runs(work_item, WorkItem.PHASE_REFINE)
            payload = existing if isinstance(existing, dict) else empty_payload(WorkItem.PHASE_REFINE)
            payload.setdefault("meta", {})
            payload["meta"].setdefault("phase", WorkItem.PHASE_REFINE)
            payload["meta"]["phase"] = WorkItem.PHASE_REFINE
            payload.setdefault("intent", {})
            payload.setdefault("explore", {})
            payload.setdefault("parked_for_later", {})
            payload.setdefault("artefacts", {})
            payload.setdefault("validation", {})

            payload["canonical_summary"] = str(request.POST.get("refine_canonical_summary") or "").strip()
            payload["intent"]["destination"] = str(request.POST.get("refine_destination") or "").strip()
            payload["intent"]["success_criteria"] = _multiline_to_list(request.POST.get("refine_success_criteria") or "")
            payload["intent"]["constraints"] = _multiline_to_list(request.POST.get("refine_constraints") or "")
            payload["intent"]["non_goals"] = _multiline_to_list(request.POST.get("refine_non_goals") or "")
            payload["intent"]["assumptions"] = _multiline_to_list(request.POST.get("refine_assumptions") or "")
            payload["intent"]["open_questions"] = _multiline_to_list(request.POST.get("refine_open_questions") or "")
            payload["explore"]["adjacent_ideas"] = _multiline_to_list(request.POST.get("refine_adjacent_ideas") or "")
            payload["explore"]["risks"] = _multiline_to_list(request.POST.get("refine_risks") or "")
            payload["explore"]["tradeoffs"] = _multiline_to_list(request.POST.get("refine_tradeoffs") or "")
            payload["explore"]["reframes"] = _multiline_to_list(request.POST.get("refine_reframes") or "")
            payload["parked_for_later"]["items"] = [
                {"title": value, "detail": ""}
                for value in _multiline_to_list(request.POST.get("refine_parked_items") or "")
            ]

            ok_schema, schema_errors = validate_structural(payload)
            ok_phase, phase_errors = check_required_nonempty(payload, phase=WorkItem.PHASE_REFINE)
            if not ok_schema or not ok_phase:
                error_text = "; ".join([str(e) for e in list(schema_errors or []) + list(phase_errors or []) if str(e).strip()])
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": error_text or "Validation failed."}, status=400)
                messages.error(request, error_text or "Validation failed.")
                return redirect("projects:derax_project_home", project_id=project.id)

            persist_derax_payload(work_item=work_item, payload=payload, user=request.user, chat=None)
            if payload["intent"]["destination"]:
                work_item.intent_raw = str(payload["intent"]["destination"] or "").strip()
                work_item.save(update_fields=["intent_raw", "updated_at"])
            if _is_ajax(request):
                return JsonResponse({"ok": True, "refine_text": _readable_derax_text(json.dumps(payload, ensure_ascii=True, indent=2))})
            messages.success(request, "REFINE fields saved.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "save_active_stage_structured":
            phase = str(work_item.active_phase or "").strip().upper() or WorkItem.PHASE_DEFINE
            if phase == WorkItem.PHASE_REFINE:
                existing = _latest_payload_from_runs(work_item, WorkItem.PHASE_REFINE)
            elif phase == WorkItem.PHASE_EXPLORE:
                existing = _latest_payload_for_phase(work_item, WorkItem.PHASE_EXPLORE)
            elif phase == WorkItem.PHASE_DEFINE:
                existing = _latest_payload_for_phase(work_item, WorkItem.PHASE_DEFINE)
            else:
                existing = _latest_payload_from_runs(work_item, phase)
            payload = existing if isinstance(existing, dict) else empty_payload(phase)
            payload.setdefault("meta", {})
            payload["meta"]["phase"] = phase
            payload.setdefault("intent", {})
            payload.setdefault("explore", {})
            payload.setdefault("parked_for_later", {})
            payload.setdefault("artefacts", {})
            payload.setdefault("validation", {})
            payload["canonical_summary"] = str(request.POST.get("stage_canonical_summary") or "").strip()
            payload["intent"]["destination"] = str(request.POST.get("stage_destination") or "").strip()
            payload["intent"]["success_criteria"] = _multiline_to_list(request.POST.get("stage_success_criteria") or "")
            payload["intent"]["constraints"] = _multiline_to_list(request.POST.get("stage_constraints") or "")
            payload["intent"]["non_goals"] = _multiline_to_list(request.POST.get("stage_non_goals") or "")
            payload["intent"]["assumptions"] = _multiline_to_list(request.POST.get("stage_assumptions") or "")
            payload["intent"]["open_questions"] = _multiline_to_list(request.POST.get("stage_open_questions") or "")
            payload["explore"]["adjacent_ideas"] = _multiline_to_list(request.POST.get("stage_adjacent_ideas") or "")
            payload["explore"]["risks"] = _multiline_to_list(request.POST.get("stage_risks") or "")
            payload["explore"]["tradeoffs"] = _multiline_to_list(request.POST.get("stage_tradeoffs") or "")
            payload["explore"]["reframes"] = _multiline_to_list(request.POST.get("stage_reframes") or "")
            payload["parked_for_later"]["items"] = [
                {"title": value, "detail": ""}
                for value in _multiline_to_list(request.POST.get("stage_parked_items") or "")
            ]
            if phase == WorkItem.PHASE_EXECUTE:
                payload["artefacts"]["proposed"] = _multiline_to_execute_proposed(
                    request.POST.get("stage_execute_proposed") or ""
                )
            if phase == WorkItem.PHASE_DEFINE:
                payload = _sanitise_define_payload(payload)
            elif phase == WorkItem.PHASE_EXECUTE:
                payload = _sanitise_execute_payload(payload)
            ok_schema, schema_errors = validate_structural(payload)
            ok_phase, phase_errors = check_required_nonempty(payload, phase=phase)
            if not ok_schema or not ok_phase:
                error_text = "; ".join([str(e) for e in list(schema_errors or []) + list(phase_errors or []) if str(e).strip()])
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": error_text or "Validation failed."}, status=400)
                messages.error(request, error_text or "Validation failed.")
                return redirect("projects:derax_project_home", project_id=project.id)
            persist_derax_payload(work_item=work_item, payload=payload, user=request.user, chat=None)
            now_iso = timezone.now().isoformat()
            payload_text = json.dumps(payload, ensure_ascii=True, indent=2)
            if phase == WorkItem.PHASE_DEFINE:
                hist = [h for h in list(work_item.derax_define_history or []) if isinstance(h, dict)]
                hist.append({"role": "assistant", "text": payload_text, "timestamp": now_iso})
                work_item.derax_define_history = hist[-40:]
                work_item.save(update_fields=["derax_define_history", "updated_at"])
            elif phase == WorkItem.PHASE_EXPLORE:
                hist = [h for h in list(work_item.derax_explore_history or []) if isinstance(h, dict)]
                hist.append({"role": "assistant", "text": payload_text, "timestamp": now_iso})
                work_item.derax_explore_history = hist[-40:]
                work_item.save(update_fields=["derax_explore_history", "updated_at"])
            if payload["intent"]["destination"]:
                work_item.intent_raw = str(payload["intent"]["destination"] or "").strip()
                work_item.save(update_fields=["intent_raw", "updated_at"])
            if _is_ajax(request):
                return JsonResponse({"ok": True, "latest_text": _readable_derax_text(payload_text)})
            messages.success(request, f"{phase} fields saved.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "project_doc_archive":
            if not can_archive_project_docs:
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": "Only the project owner can archive files."}, status=403)
                messages.error(request, "Only the project owner can archive files.")
                return redirect("projects:derax_project_home", project_id=project.id)
            doc_id_raw = (request.POST.get("doc_id") or "").strip()
            if not doc_id_raw.isdigit():
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": "Invalid project document."}, status=400)
                messages.error(request, "Invalid project document.")
                return redirect("projects:derax_project_home", project_id=project.id)
            row = ProjectDocument.objects.filter(project=project, id=int(doc_id_raw), is_archived=False).first()
            if not row:
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": "Project document not found."}, status=404)
                messages.error(request, "Project document not found.")
                return redirect("projects:derax_project_home", project_id=project.id)
            row.is_archived = True
            row.archived_at = timezone.now()
            row.archived_by = request.user
            row.save(update_fields=["is_archived", "archived_at", "archived_by", "updated_at"])
            AuditLog.objects.create(
                project=project,
                actor=request.user,
                event_type="PROJECT_DOC_ARCHIVED",
                entity_type="ProjectDocument",
                entity_id=str(row.id),
                field_changes={"is_archived": {"before": False, "after": True}},
                summary=f"Archived project file: {row.original_name or row.title or row.id}",
                source=AuditLog.Source.UI,
            )
            if _is_ajax(request):
                return JsonResponse({"ok": True, "doc_id": row.id, "action": "archived"})
            messages.success(request, "Project file archived.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "project_doc_delete":
            if not can_hard_delete_project_docs:
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": "Only admin users can permanently delete files."}, status=403)
                messages.error(request, "Only admin users can permanently delete files.")
                return redirect("projects:derax_project_home", project_id=project.id)
            doc_id_raw = (request.POST.get("doc_id") or "").strip()
            if not doc_id_raw.isdigit():
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": "Invalid project document."}, status=400)
                messages.error(request, "Invalid project document.")
                return redirect("projects:derax_project_home", project_id=project.id)
            row = ProjectDocument.objects.filter(project=project, id=int(doc_id_raw)).first()
            if not row:
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": "Project document not found."}, status=404)
                messages.error(request, "Project document not found.")
                return redirect("projects:derax_project_home", project_id=project.id)
            row_id = row.id
            row_name = row.original_name or row.title or str(row.id)
            was_archived = bool(row.is_archived)
            try:
                row.file.delete(save=False)
            except Exception:
                pass
            row.delete()
            AuditLog.objects.create(
                project=project,
                actor=request.user,
                event_type="PROJECT_DOC_HARD_DELETED",
                entity_type="ProjectDocument",
                entity_id=str(row_id),
                field_changes={"is_archived": {"before": was_archived, "after": True}},
                summary=f"Permanently deleted project file: {row_name}",
                source=AuditLog.Source.ADMIN,
            )
            if _is_ajax(request):
                return JsonResponse({"ok": True, "doc_id": row_id, "action": "deleted"})
            messages.success(request, "Project file deleted.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "save_phase_contract_text":
            if not can_edit_phase_contracts:
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": "Permission denied."}, status=403)
                messages.error(request, "Permission denied.")
                return redirect("projects:derax_project_home", project_id=project.id)
            active_phase_name = str(work_item.active_phase or "").strip().upper() or WorkItem.PHASE_DEFINE
            phase_name = str(request.POST.get("contract_phase") or "").strip().upper() or active_phase_name
            if phase_name != active_phase_name:
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": "Only the active stage contract can be edited."}, status=400)
                messages.error(request, "Only the active stage contract can be edited.")
                return redirect("projects:derax_project_home", project_id=project.id)
            if phase_name not in WorkItem.ALLOWED_PHASES:
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": "Invalid phase."}, status=400)
                messages.error(request, "Invalid phase.")
                return redirect("projects:derax_project_home", project_id=project.id)
            contract_key = _phase_contract_key(phase_name)
            text = str(request.POST.get("contract_text") or "")
            row = (
                ContractText.objects.filter(
                    key=contract_key,
                    scope_type=ContractText.ScopeType.PROJECT_USER,
                    scope_project_id=project.id,
                    scope_user_id=request.user.id,
                    status=ContractText.Status.ACTIVE,
                )
                .order_by("-updated_at", "-id")
                .first()
            )
            if row is None:
                ContractText.objects.create(
                    key=contract_key,
                    scope_type=ContractText.ScopeType.PROJECT_USER,
                    scope_project_id=project.id,
                    scope_user_id=request.user.id,
                    status=ContractText.Status.ACTIVE,
                    text=text,
                    updated_by=request.user,
                )
            else:
                row.text = text
                row.updated_by = request.user
                row.save(update_fields=["text", "updated_by", "updated_at"])
            effective_text, source = _phase_contract_effective_text(
                user=request.user,
                phase_name=phase_name,
                project_id=project.id,
            )
            return JsonResponse({"ok": True, "phase": phase_name, "effective_text": effective_text, "source": source})

        if action == "reset_phase_contract_text":
            if not can_edit_phase_contracts:
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": "Permission denied."}, status=403)
                messages.error(request, "Permission denied.")
                return redirect("projects:derax_project_home", project_id=project.id)
            active_phase_name = str(work_item.active_phase or "").strip().upper() or WorkItem.PHASE_DEFINE
            phase_name = str(request.POST.get("contract_phase") or "").strip().upper() or active_phase_name
            if phase_name != active_phase_name:
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": "Only the active stage contract can be reset."}, status=400)
                messages.error(request, "Only the active stage contract can be reset.")
                return redirect("projects:derax_project_home", project_id=project.id)
            if phase_name not in WorkItem.ALLOWED_PHASES:
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": "Invalid phase."}, status=400)
                messages.error(request, "Invalid phase.")
                return redirect("projects:derax_project_home", project_id=project.id)
            contract_key = _phase_contract_key(phase_name)
            ContractText.objects.filter(
                key=contract_key,
                scope_type=ContractText.ScopeType.PROJECT_USER,
                scope_project_id=project.id,
                scope_user_id=request.user.id,
                status=ContractText.Status.ACTIVE,
            ).update(status=ContractText.Status.RETIRED, updated_by=request.user)
            effective_text, source = _phase_contract_effective_text(
                user=request.user,
                phase_name=phase_name,
                project_id=project.id,
            )
            return JsonResponse({"ok": True, "phase": phase_name, "effective_text": effective_text, "source": source})

        if action in {"autosave_end_in_mind", "save_end_in_mind"}:
            end_in_mind = str(request.POST.get("end_in_mind") or "").strip()
            work_item.intent_raw = end_in_mind
            if end_in_mind and not str(work_item.title or "").strip():
                work_item.title = end_in_mind[:200]
            work_item.save(update_fields=["intent_raw", "title", "updated_at"])
            work_item.append_activity(
                actor=request.user,
                action="end_in_mind_saved",
                notes="DEFINE end-in-mind autosaved.",
            )
            if _is_ajax(request):
                return JsonResponse({"ok": True, "saved_text": end_in_mind})
            messages.success(request, "End in mind saved.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action in {"autosave_refine_input", "save_refine_input"}:
            refine_input = str(request.POST.get("refine_input") or "").strip()
            work_item.intent_raw = refine_input
            if refine_input and not str(work_item.title or "").strip():
                work_item.title = refine_input[:200]
            work_item.save(update_fields=["intent_raw", "title", "updated_at"])
            work_item.append_activity(
                actor=request.user,
                action="refine_input_saved",
                notes="REFINE input saved.",
            )
            if _is_ajax(request):
                return JsonResponse({"ok": True, "saved_text": refine_input})
            messages.success(request, "Refine input saved.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "use_define_response_as_intent":
            candidate = str(request.POST.get("candidate_text") or "").strip()
            if not candidate:
                candidate = _latest_define_assistant_text(work_item)
            candidate = _extract_end_in_mind(candidate)
            if not candidate:
                messages.error(request, "No DEFINE response available to use as intent.")
                return redirect("projects:derax_project_home", project_id=project.id)
            work_item.intent_raw = candidate
            if not str(work_item.title or "").strip():
                work_item.title = candidate[:200]
            work_item.save(update_fields=["intent_raw", "title", "updated_at"])
            work_item.append_activity(
                actor=request.user,
                action="end_in_mind_from_define",
                notes="Intent updated from latest DEFINE response.",
            )
            messages.success(request, "DEFINE response applied to intent.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "use_explore_response_as_destination":
            candidate = str(request.POST.get("candidate_text") or "").strip()
            if not candidate:
                candidate = _latest_explore_assistant_text(work_item)
            candidate = _extract_end_in_mind(candidate)
            if not candidate:
                messages.error(request, "No EXPLORE response available to use as destination.")
                return redirect("projects:derax_project_home", project_id=project.id)
            work_item.intent_raw = candidate
            if not str(work_item.title or "").strip():
                work_item.title = candidate[:200]
            work_item.save(update_fields=["intent_raw", "title", "updated_at"])
            work_item.append_activity(
                actor=request.user,
                action="destination_from_explore",
                notes="Destination updated from latest EXPLORE response.",
            )
            messages.success(request, "EXPLORE response applied to destination.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "use_refine_response_as_input":
            candidate = str(request.POST.get("candidate_text") or "").strip()
            if not candidate:
                latest_refine_payload = _latest_payload_from_runs(work_item, WorkItem.PHASE_REFINE)
                if latest_refine_payload:
                    candidate = _readable_derax_text(json.dumps(latest_refine_payload, ensure_ascii=True, indent=2))
            if not candidate:
                messages.error(request, "No REFINE response available to apply.")
                return redirect("projects:derax_project_home", project_id=project.id)
            work_item.intent_raw = candidate
            if not str(work_item.title or "").strip():
                work_item.title = candidate[:200]
            work_item.save(update_fields=["intent_raw", "title", "updated_at"])
            work_item.append_activity(
                actor=request.user,
                action="refine_input_from_refine_response",
                notes="Refine input updated from latest REFINE response.",
            )
            messages.success(request, "REFINE response applied to refine input.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "lock_refine_stage":
            if str(work_item.active_phase or "").strip().upper() != WorkItem.PHASE_REFINE:
                messages.error(request, "REFINE can only be locked while active phase is REFINE.")
                return redirect("projects:derax_project_home", project_id=project.id)
            candidate = str(request.POST.get("lock_seed_text") or "").strip()
            if not candidate:
                candidate = str(request.POST.get("refine_input") or "").strip()
            if not candidate:
                candidate = str(request.POST.get("candidate_text") or "").strip()
            if not candidate:
                candidate = _latest_refine_response_text(work_item)
            if not candidate:
                messages.error(request, "No REFINE response available to lock.")
                return redirect("projects:derax_project_home", project_id=project.id)
            try:
                work_item.append_seed_revision(
                    seed_text=candidate,
                    created_by=request.user,
                    reason="REFINE_LOCKED",
                )
                work_item.set_phase(WorkItem.PHASE_APPROVE)
            except Exception as exc:
                messages.error(request, str(exc))
                return redirect("projects:derax_project_home", project_id=project.id)
            work_item.intent_raw = candidate
            work_item.save(update_fields=["intent_raw", "updated_at"])
            messages.success(request, "REFINE locked to history. Phase moved to APPROVE.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "lock_approve_and_execute":
            if str(work_item.active_phase or "").strip().upper() != WorkItem.PHASE_APPROVE:
                messages.error(request, "APPROVE can only be locked while active phase is APPROVE.")
                return redirect("projects:derax_project_home", project_id=project.id)
            candidate = str(request.POST.get("phase_user_input") or "").strip()
            if not candidate:
                candidate_payload = _latest_payload_from_runs(work_item, WorkItem.PHASE_APPROVE)
                if candidate_payload:
                    candidate = _readable_derax_text(json.dumps(candidate_payload, ensure_ascii=True, indent=2))
            if not candidate:
                messages.error(request, "No APPROVE response available to lock.")
                return redirect("projects:derax_project_home", project_id=project.id)
            try:
                work_item.append_seed_revision(
                    seed_text=candidate,
                    created_by=request.user,
                    reason="APPROVE_LOCKED",
                )
            except Exception as exc:
                messages.error(request, str(exc))
                return redirect("projects:derax_project_home", project_id=project.id)
            work_item.active_phase = WorkItem.PHASE_EXECUTE
            work_item.intent_raw = candidate
            work_item.save(update_fields=["active_phase", "intent_raw", "updated_at"])
            work_item.append_activity(
                actor=request.user,
                action="phase_changed",
                notes="APPROVE -> EXECUTE (manual lock)",
            )
            messages.success(request, "APPROVE locked to history. Phase moved to EXECUTE.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "lock_execute_stage":
            if str(work_item.active_phase or "").strip().upper() != WorkItem.PHASE_EXECUTE:
                messages.error(request, "EXECUTE can only be locked while active phase is EXECUTE.")
                return redirect("projects:derax_project_home", project_id=project.id)
            candidate = str(request.POST.get("phase_user_input") or "").strip()
            if not candidate:
                candidate_payload = _latest_payload_from_runs(work_item, WorkItem.PHASE_EXECUTE)
                if candidate_payload:
                    candidate = _readable_derax_text(json.dumps(candidate_payload, ensure_ascii=True, indent=2))
            if not candidate:
                candidate = str(work_item.intent_raw or "").strip()
            if not candidate:
                messages.error(request, "No EXECUTE response available to lock.")
                return redirect("projects:derax_project_home", project_id=project.id)
            try:
                work_item.append_seed_revision(
                    seed_text=candidate,
                    created_by=request.user,
                    reason="EXECUTE_LOCKED",
                )
            except Exception as exc:
                messages.error(request, str(exc))
                return redirect("projects:derax_project_home", project_id=project.id)
            work_item.intent_raw = candidate
            work_item.save(update_fields=["intent_raw", "updated_at"])
            messages.success(request, "EXECUTE locked to history.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "define_llm_turn":
            user_input = str(request.POST.get("phase_user_input") or request.POST.get("define_user_input") or "").strip()
            if not user_input:
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": "Enter text for the DEFINE LLM turn."}, status=400)
                messages.error(request, "Enter text for the DEFINE LLM turn.")
                return redirect("projects:derax_project_home", project_id=project.id)

            history_entries = [h for h in list(work_item.derax_define_history or []) if isinstance(h, dict)]
            history_entries = history_entries[-40:]
            messages_list = []
            for row in history_entries:
                role = str(row.get("role") or "").strip().lower()
                if role not in {"user", "assistant"}:
                    continue
                text = str(row.get("text") or "").strip()
                if not text:
                    continue
                messages_list.append({"role": role, "content": text})
            messages_list.append({"role": "user", "content": user_input})

            effective_context = {}
            try:
                effective_context = dict(
                    resolve_effective_context(
                        project_id=project.id,
                        user_id=request.user.id,
                        session_overrides={},
                        chat_overrides={},
                    )
                    or {}
                )
            except Exception:
                effective_context = {}

            original_phase = str(work_item.active_phase or "")
            try:
                work_item.active_phase = WorkItem.PHASE_DEFINE
                contract_ctx = ContractContext(
                    user=request.user,
                    project=project,
                    work_item=work_item,
                    active_phase=WorkItem.PHASE_DEFINE,
                    user_text=user_input,
                    effective_context=effective_context,
                    is_derax=True,
                    legacy_system_blocks=[
                        "DEFINE TURN MODE: Focus only on defining end-in-mind intent. "
                        "Do not produce plans, architecture, implementation steps, or delivery sequencing."
                    ],
                    include_envelope=False,
                    strict_json=False,
                )
                llm_text = generate_text(
                    system_blocks=[],
                    messages=messages_list,
                    user=request.user,
                    contract_ctx=contract_ctx,
                )
                ok, payload_or_error = validate_derax_response(str(llm_text or ""))
                if not ok:
                    correction = derax_json_correction_prompt(str(payload_or_error or ""), phase=WorkItem.PHASE_DEFINE)
                    llm_text = generate_text(
                        system_blocks=[],
                        messages=messages_list + [
                            {"role": "assistant", "content": str(llm_text or "")},
                            {"role": "user", "content": correction},
                        ],
                        user=request.user,
                        contract_ctx=contract_ctx,
                    )
                    ok, payload_or_error = validate_derax_response(str(llm_text or ""))
                if not ok:
                    raise ValueError("DERAX DEFINE response invalid JSON schema: " + str(payload_or_error or ""))
            except Exception as exc:
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": f"DEFINE LLM turn failed: {exc}"}, status=500)
                messages.error(request, f"DEFINE LLM turn failed: {exc}")
                work_item.active_phase = original_phase
                return redirect("projects:derax_project_home", project_id=project.id)
            finally:
                work_item.active_phase = original_phase

            payload = _as_dict(payload_or_error)
            payload = _sanitise_define_payload(payload)
            persist_derax_payload(work_item=work_item, payload=payload, user=request.user, chat=None)
            now_iso = timezone.now().isoformat()
            history_entries.append({"role": "user", "text": user_input, "timestamp": now_iso})
            history_entries.append({"role": "assistant", "text": json.dumps(payload, ensure_ascii=True, indent=2), "timestamp": now_iso})
            history_entries = history_entries[-40:]
            work_item.derax_define_history = history_entries
            work_item.save(update_fields=["derax_define_history", "updated_at"])
            work_item.append_activity(
                actor=request.user,
                action="define_llm_turn",
                notes="DEFINE turn recorded.",
            )
            if _is_ajax(request):
                define_history = _build_phase_history_rows(list(work_item.derax_define_history or []))
                latest_text = _extract_end_in_mind(_latest_define_assistant_text(work_item))
                history_html = render_to_string(
                    "projects/_derax_define_history.html",
                    {
                        "define_history": define_history,
                    },
                    request=request,
                )
                return JsonResponse(
                    {
                        "ok": True,
                        "history_html": history_html,
                        "latest_define_assistant_text": latest_text,
                    }
                )
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "explore_llm_turn":
            user_input = str(
                request.POST.get("phase_user_input")
                or request.POST.get("explore_user_input")
                or ""
            ).strip()
            if not user_input:
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": "Enter text for the EXPLORE LLM turn."}, status=400)
                messages.error(request, "Enter text for the EXPLORE LLM turn.")
                return redirect("projects:derax_project_home", project_id=project.id)

            history_entries = [h for h in list(work_item.derax_explore_history or []) if isinstance(h, dict)]
            history_entries = history_entries[-40:]
            messages_list = []
            for row in history_entries:
                role = str(row.get("role") or "").strip().lower()
                if role not in {"user", "assistant"}:
                    continue
                text = str(row.get("text") or "").strip()
                if not text:
                    continue
                messages_list.append({"role": role, "content": text})
            messages_list.append({"role": "user", "content": user_input})

            effective_context = {}
            try:
                effective_context = dict(
                    resolve_effective_context(
                        project_id=project.id,
                        user_id=request.user.id,
                        session_overrides={},
                        chat_overrides={},
                    )
                    or {}
                )
            except Exception:
                effective_context = {}

            original_phase = str(work_item.active_phase or "")
            try:
                work_item.active_phase = WorkItem.PHASE_EXPLORE
                contract_ctx = ContractContext(
                    user=request.user,
                    project=project,
                    work_item=work_item,
                    active_phase=WorkItem.PHASE_EXPLORE,
                    user_text=user_input,
                    effective_context=effective_context,
                    is_derax=True,
                    legacy_system_blocks=[
                        "EXPLORE TURN MODE: Challenge and stress-test the destination only. "
                        "Do not produce route plans, tasks, timelines, or implementation structures."
                    ],
                    include_envelope=False,
                    strict_json=False,
                )
                llm_text = generate_text(
                    system_blocks=[],
                    messages=messages_list,
                    user=request.user,
                    contract_ctx=contract_ctx,
                )
                ok, payload_or_error = validate_derax_response(str(llm_text or ""))
                if not ok:
                    parsed_payload = _payload_candidate_from_text(str(llm_text or ""))
                    if isinstance(parsed_payload, dict):
                        recovered_ok, recovered_payload, recovered_error = _phase_payload_recovered(
                            parsed_payload,
                            phase=WorkItem.PHASE_EXPLORE,
                        )
                        if recovered_ok:
                            ok = True
                            payload_or_error = recovered_payload
                        else:
                            payload_or_error = recovered_error or payload_or_error
                if not ok:
                    correction = derax_json_correction_prompt(str(payload_or_error or ""), phase=WorkItem.PHASE_EXPLORE)
                    llm_text = generate_text(
                        system_blocks=[],
                        messages=messages_list + [
                            {"role": "assistant", "content": str(llm_text or "")},
                            {"role": "user", "content": correction},
                        ],
                        user=request.user,
                        contract_ctx=contract_ctx,
                    )
                    ok, payload_or_error = validate_derax_response(str(llm_text or ""))
                    if not ok:
                        parsed_payload = _payload_candidate_from_text(str(llm_text or ""))
                        if isinstance(parsed_payload, dict):
                            recovered_ok, recovered_payload, recovered_error = _phase_payload_recovered(
                                parsed_payload,
                                phase=WorkItem.PHASE_EXPLORE,
                            )
                            if recovered_ok:
                                ok = True
                                payload_or_error = recovered_payload
                            else:
                                payload_or_error = recovered_error or payload_or_error
                if not ok:
                    raise ValueError("DERAX EXPLORE response invalid JSON schema: " + str(payload_or_error or ""))
            except Exception as exc:
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": f"EXPLORE LLM turn failed: {exc}"}, status=500)
                messages.error(request, f"EXPLORE LLM turn failed: {exc}")
                work_item.active_phase = original_phase
                return redirect("projects:derax_project_home", project_id=project.id)
            finally:
                work_item.active_phase = original_phase

            payload = _as_dict(payload_or_error)
            persist_derax_payload(work_item=work_item, payload=payload, user=request.user, chat=None)
            now_iso = timezone.now().isoformat()
            history_entries.append({"role": "user", "text": user_input, "timestamp": now_iso})
            history_entries.append({"role": "assistant", "text": json.dumps(payload, ensure_ascii=True, indent=2), "timestamp": now_iso})
            history_entries = history_entries[-40:]
            work_item.derax_explore_history = history_entries
            work_item.save(update_fields=["derax_explore_history", "updated_at"])
            work_item.append_activity(
                actor=request.user,
                action="explore_llm_turn",
                notes="EXPLORE turn recorded.",
            )
            if _is_ajax(request):
                explore_history = _build_phase_history_rows(list(work_item.derax_explore_history or []))
                latest_text = _extract_end_in_mind(_latest_explore_assistant_text(work_item))
                history_html = render_to_string(
                    "projects/_derax_define_history.html",
                    {
                        "define_history": explore_history,
                    },
                    request=request,
                )
                return JsonResponse(
                    {
                        "ok": True,
                        "history_html": history_html,
                        "latest_define_assistant_text": latest_text,
                    }
                )
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "refine_llm_turn":
            user_input = str(
                request.POST.get("phase_user_input")
                or request.POST.get("refine_input")
                or ""
            ).strip()
            if not user_input:
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": "Enter text for the REFINE LLM turn."}, status=400)
                messages.error(request, "Enter text for the REFINE LLM turn.")
                return redirect("projects:derax_project_home", project_id=project.id)

            history_entries = _merged_derax_audit_history(work_item)[-40:]
            messages_list = []
            for row in history_entries:
                role = str(row.get("role") or "").strip().lower()
                if role not in {"user", "assistant"}:
                    continue
                text = str(row.get("text") or "").strip()
                if not text:
                    continue
                messages_list.append({"role": role, "content": text})
            messages_list.append({"role": "user", "content": user_input})

            effective_context = {}
            try:
                effective_context = dict(
                    resolve_effective_context(
                        project_id=project.id,
                        user_id=request.user.id,
                        session_overrides={},
                        chat_overrides={},
                    )
                    or {}
                )
            except Exception:
                effective_context = {}

            original_phase = str(work_item.active_phase or "")
            try:
                work_item.active_phase = WorkItem.PHASE_REFINE
                contract_ctx = ContractContext(
                    user=request.user,
                    project=project,
                    work_item=work_item,
                    active_phase=WorkItem.PHASE_REFINE,
                    user_text=user_input,
                    effective_context=effective_context,
                    is_derax=True,
                    legacy_system_blocks=[
                        "REFINE TURN MODE: Synthesize and tighten destination pack only. "
                        "Do not produce route plans, task breakdowns, timelines, or implementation structures."
                    ],
                    include_envelope=False,
                    strict_json=False,
                )
                llm_text = generate_text(
                    system_blocks=[],
                    messages=messages_list,
                    user=request.user,
                    contract_ctx=contract_ctx,
                )
                ok, payload_or_error = validate_derax_response(str(llm_text or ""))
                if not ok:
                    parsed_payload = _payload_candidate_from_text(str(llm_text or ""))
                    if isinstance(parsed_payload, dict):
                        recovered_ok, recovered_payload, recovered_error = _phase_payload_recovered(
                            parsed_payload,
                            phase=WorkItem.PHASE_REFINE,
                        )
                        if (not recovered_ok) and isinstance(recovered_payload, dict):
                            explore_seed = _latest_payload_for_phase(work_item, WorkItem.PHASE_EXPLORE)
                            if isinstance(explore_seed, dict) and explore_seed:
                                merged_payload = _backfill_refine_from_explore(
                                    refine_payload=recovered_payload,
                                    explore_payload=explore_seed,
                                )
                                merged_ok, merged_payload2, merged_error = _phase_payload_recovered(
                                    merged_payload,
                                    phase=WorkItem.PHASE_REFINE,
                                )
                                if merged_ok:
                                    recovered_ok = True
                                    recovered_payload = merged_payload2
                                else:
                                    recovered_error = merged_error or recovered_error
                        if recovered_ok:
                            ok = True
                            payload_or_error = recovered_payload
                        else:
                            payload_or_error = recovered_error or payload_or_error
                if not ok:
                    correction = derax_json_correction_prompt(str(payload_or_error or ""), phase=WorkItem.PHASE_REFINE)
                    llm_text = generate_text(
                        system_blocks=[],
                        messages=messages_list + [
                            {"role": "assistant", "content": str(llm_text or "")},
                            {"role": "user", "content": correction},
                        ],
                        user=request.user,
                        contract_ctx=contract_ctx,
                    )
                    ok, payload_or_error = validate_derax_response(str(llm_text or ""))
                    if not ok:
                        parsed_payload = _payload_candidate_from_text(str(llm_text or ""))
                        if isinstance(parsed_payload, dict):
                            recovered_ok, recovered_payload, recovered_error = _phase_payload_recovered(
                                parsed_payload,
                                phase=WorkItem.PHASE_REFINE,
                            )
                            if (not recovered_ok) and isinstance(recovered_payload, dict):
                                explore_seed = _latest_payload_for_phase(work_item, WorkItem.PHASE_EXPLORE)
                                if isinstance(explore_seed, dict) and explore_seed:
                                    merged_payload = _backfill_refine_from_explore(
                                        refine_payload=recovered_payload,
                                        explore_payload=explore_seed,
                                    )
                                    merged_ok, merged_payload2, merged_error = _phase_payload_recovered(
                                        merged_payload,
                                        phase=WorkItem.PHASE_REFINE,
                                    )
                                    if merged_ok:
                                        recovered_ok = True
                                        recovered_payload = merged_payload2
                                    else:
                                        recovered_error = merged_error or recovered_error
                            if recovered_ok:
                                ok = True
                                payload_or_error = recovered_payload
                            else:
                                payload_or_error = recovered_error or payload_or_error
                if not ok:
                    debug_shape = _payload_shape_debug(str(llm_text or ""))
                    raise ValueError(
                        "DERAX REFINE response invalid JSON schema: "
                        + str(payload_or_error or "")
                        + f" [{debug_shape}]"
                    )
            except Exception as exc:
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": f"REFINE LLM turn failed: {exc}"}, status=500)
                messages.error(request, f"REFINE LLM turn failed: {exc}")
                work_item.active_phase = original_phase
                return redirect("projects:derax_project_home", project_id=project.id)
            finally:
                work_item.active_phase = original_phase

            payload = _as_dict(payload_or_error)
            persist_derax_payload(work_item=work_item, payload=payload, user=request.user, chat=None)
            refined_text = _readable_derax_text(json.dumps(payload, ensure_ascii=True, indent=2))
            work_item.append_activity(
                actor=request.user,
                action="refine_llm_turn",
                notes="REFINE turn recorded.",
            )
            if _is_ajax(request):
                return JsonResponse(
                    {
                        "ok": True,
                        "refine_text": refined_text,
                    }
                )
            messages.success(request, "REFINE response generated.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "approve_llm_turn":
            user_input = str(request.POST.get("phase_user_input") or "").strip()
            if not user_input:
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": "Enter text for the APPROVE LLM turn."}, status=400)
                messages.error(request, "Enter text for the APPROVE LLM turn.")
                return redirect("projects:derax_project_home", project_id=project.id)

            history_entries = _merged_derax_audit_history(work_item)[-40:]
            messages_list = []
            for row in history_entries:
                role = str(row.get("role") or "").strip().lower()
                if role not in {"user", "assistant"}:
                    continue
                text = str(row.get("text") or "").strip()
                if not text:
                    continue
                messages_list.append({"role": role, "content": text})
            messages_list.append({"role": "user", "content": user_input})

            effective_context = {}
            try:
                effective_context = dict(
                    resolve_effective_context(
                        project_id=project.id,
                        user_id=request.user.id,
                        session_overrides={},
                        chat_overrides={},
                    )
                    or {}
                )
            except Exception:
                effective_context = {}

            original_phase = str(work_item.active_phase or "")
            try:
                work_item.active_phase = WorkItem.PHASE_APPROVE
                contract_ctx = ContractContext(
                    user=request.user,
                    project=project,
                    work_item=work_item,
                    active_phase=WorkItem.PHASE_APPROVE,
                    user_text=user_input,
                    effective_context=effective_context,
                    is_derax=True,
                    legacy_system_blocks=[
                        "APPROVE TURN MODE: Validate stability only. "
                        "Do not produce route plans, task breakdowns, timelines, or implementation structures."
                    ],
                    include_envelope=False,
                    strict_json=False,
                )
                llm_text = generate_text(
                    system_blocks=[],
                    messages=messages_list,
                    user=request.user,
                    contract_ctx=contract_ctx,
                )
                ok, payload_or_error = validate_derax_response(str(llm_text or ""))
                if not ok:
                    parsed_payload = _payload_candidate_from_text(str(llm_text or ""))
                    if isinstance(parsed_payload, dict):
                        recovered_ok, recovered_payload, recovered_error = _phase_payload_recovered(
                            parsed_payload,
                            phase=WorkItem.PHASE_APPROVE,
                        )
                        if (not recovered_ok) and isinstance(recovered_payload, dict):
                            refine_seed = _latest_payload_from_runs(work_item, WorkItem.PHASE_REFINE)
                            if isinstance(refine_seed, dict) and refine_seed:
                                merged_payload = _backfill_approve_from_refine(
                                    approve_payload=recovered_payload,
                                    refine_payload=refine_seed,
                                )
                                merged_ok, merged_payload2, merged_error = _phase_payload_recovered(
                                    merged_payload,
                                    phase=WorkItem.PHASE_APPROVE,
                                )
                                if merged_ok:
                                    recovered_ok = True
                                    recovered_payload = merged_payload2
                                else:
                                    recovered_error = merged_error or recovered_error
                        if recovered_ok:
                            ok = True
                            payload_or_error = recovered_payload
                        else:
                            payload_or_error = recovered_error or payload_or_error
                if not ok:
                    correction = derax_json_correction_prompt(str(payload_or_error or ""), phase=WorkItem.PHASE_APPROVE)
                    llm_text = generate_text(
                        system_blocks=[],
                        messages=messages_list + [
                            {"role": "assistant", "content": str(llm_text or "")},
                            {"role": "user", "content": correction},
                        ],
                        user=request.user,
                        contract_ctx=contract_ctx,
                    )
                    ok, payload_or_error = validate_derax_response(str(llm_text or ""))
                    if not ok:
                        parsed_payload = _payload_candidate_from_text(str(llm_text or ""))
                        if isinstance(parsed_payload, dict):
                            recovered_ok, recovered_payload, recovered_error = _phase_payload_recovered(
                                parsed_payload,
                                phase=WorkItem.PHASE_APPROVE,
                            )
                            if (not recovered_ok) and isinstance(recovered_payload, dict):
                                refine_seed = _latest_payload_from_runs(work_item, WorkItem.PHASE_REFINE)
                                if isinstance(refine_seed, dict) and refine_seed:
                                    merged_payload = _backfill_approve_from_refine(
                                        approve_payload=recovered_payload,
                                        refine_payload=refine_seed,
                                    )
                                    merged_ok, merged_payload2, merged_error = _phase_payload_recovered(
                                        merged_payload,
                                        phase=WorkItem.PHASE_APPROVE,
                                    )
                                    if merged_ok:
                                        recovered_ok = True
                                        recovered_payload = merged_payload2
                                    else:
                                        recovered_error = merged_error or recovered_error
                            if recovered_ok:
                                ok = True
                                payload_or_error = recovered_payload
                            else:
                                payload_or_error = recovered_error or payload_or_error
                if not ok:
                    raise ValueError("DERAX APPROVE response invalid JSON schema: " + str(payload_or_error or ""))
            except Exception as exc:
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": f"APPROVE LLM turn failed: {exc}"}, status=500)
                messages.error(request, f"APPROVE LLM turn failed: {exc}")
                work_item.active_phase = original_phase
                return redirect("projects:derax_project_home", project_id=project.id)
            finally:
                work_item.active_phase = original_phase

            payload = _as_dict(payload_or_error)
            persist_derax_payload(work_item=work_item, payload=payload, user=request.user, chat=None)
            approved_text = _readable_derax_text(json.dumps(payload, ensure_ascii=True, indent=2))
            work_item.append_activity(
                actor=request.user,
                action="approve_llm_turn",
                notes="APPROVE turn recorded.",
            )
            if _is_ajax(request):
                return JsonResponse(
                    {
                        "ok": True,
                        "latest_define_assistant_text": approved_text,
                    }
                )
            messages.success(request, "APPROVE response generated.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "execute_llm_turn":
            user_input = str(request.POST.get("phase_user_input") or "").strip()
            if not user_input:
                guide = _execute_prompt_guide()
                if _is_ajax(request):
                    return JsonResponse({"ok": True, "latest_define_assistant_text": guide})
                messages.info(request, "Add an artefact request for EXECUTE. Guidance is shown below.")
                messages.info(request, guide)
                return redirect("projects:derax_project_home", project_id=project.id)

            history_entries = _merged_derax_audit_history(work_item)[-40:]
            messages_list = []
            for row in history_entries:
                role = str(row.get("role") or "").strip().lower()
                if role not in {"user", "assistant"}:
                    continue
                text = str(row.get("text") or "").strip()
                if not text:
                    continue
                messages_list.append({"role": role, "content": text})
            messages_list.append({"role": "user", "content": user_input})

            effective_context = {}
            try:
                effective_context = dict(
                    resolve_effective_context(
                        project_id=project.id,
                        user_id=request.user.id,
                        session_overrides={},
                        chat_overrides={},
                    )
                    or {}
                )
            except Exception:
                effective_context = {}

            original_phase = str(work_item.active_phase or "")
            try:
                work_item.active_phase = WorkItem.PHASE_EXECUTE
                contract_ctx = ContractContext(
                    user=request.user,
                    project=project,
                    work_item=work_item,
                    active_phase=WorkItem.PHASE_EXECUTE,
                    user_text=user_input,
                    effective_context=effective_context,
                    is_derax=True,
                    include_envelope=False,
                    strict_json=False,
                )
                llm_text = generate_text(
                    system_blocks=[],
                    messages=messages_list,
                    user=request.user,
                    contract_ctx=contract_ctx,
                )
                parsed_payload = _try_parse_json_payload(str(llm_text or ""))
                if isinstance(parsed_payload, dict):
                    parsed_payload = _coerce_execute_payload_shape(parsed_payload)
                    llm_text = json.dumps(parsed_payload, ensure_ascii=True)
                ok, payload_or_error = validate_derax_response(str(llm_text or ""))
                if ok and isinstance(payload_or_error, dict):
                    payload_check = payload_or_error
                    payload_phase = str((payload_check.get("meta") or {}).get("phase") or "").strip().upper()
                    phase_ok, phase_errors = check_required_nonempty(payload_check, phase=WorkItem.PHASE_EXECUTE)
                    if payload_phase != WorkItem.PHASE_EXECUTE:
                        ok = False
                        payload_or_error = (
                            f"meta.phase must be {WorkItem.PHASE_EXECUTE}; got {payload_phase or '(blank)'}"
                        )
                    elif not phase_ok:
                        ok = False
                        payload_or_error = "; ".join([str(e) for e in phase_errors if str(e).strip()])
                if not ok:
                    correction = derax_json_correction_prompt(str(payload_or_error or ""), phase=WorkItem.PHASE_EXECUTE)
                    llm_text = generate_text(
                        system_blocks=[],
                        messages=messages_list + [
                            {"role": "assistant", "content": str(llm_text or "")},
                            {"role": "user", "content": correction},
                        ],
                        user=request.user,
                        contract_ctx=contract_ctx,
                    )
                    parsed_payload = _try_parse_json_payload(str(llm_text or ""))
                    if isinstance(parsed_payload, dict):
                        parsed_payload = _coerce_execute_payload_shape(parsed_payload)
                        llm_text = json.dumps(parsed_payload, ensure_ascii=True)
                    ok, payload_or_error = validate_derax_response(str(llm_text or ""))
                    if ok and isinstance(payload_or_error, dict):
                        payload_check = payload_or_error
                        payload_phase = str((payload_check.get("meta") or {}).get("phase") or "").strip().upper()
                        phase_ok, phase_errors = check_required_nonempty(payload_check, phase=WorkItem.PHASE_EXECUTE)
                        if payload_phase != WorkItem.PHASE_EXECUTE:
                            ok = False
                            payload_or_error = (
                                f"meta.phase must be {WorkItem.PHASE_EXECUTE}; got {payload_phase or '(blank)'}"
                            )
                        elif not phase_ok:
                            ok = False
                            payload_or_error = "; ".join([str(e) for e in phase_errors if str(e).strip()])
                if not ok:
                    raise ValueError("DERAX EXECUTE response invalid JSON schema: " + str(payload_or_error or ""))
            except Exception as exc:
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": f"EXECUTE LLM turn failed: {exc}"}, status=500)
                messages.error(request, f"EXECUTE LLM turn failed: {exc}")
                work_item.active_phase = original_phase
                return redirect("projects:derax_project_home", project_id=project.id)
            finally:
                work_item.active_phase = original_phase

            payload_obj = payload_or_error if isinstance(payload_or_error, dict) else {}
            if not payload_obj:
                fallback_obj = _try_parse_json_payload(str(payload_or_error or ""))
                if isinstance(fallback_obj, dict):
                    payload_obj = fallback_obj
            payload = _sanitise_execute_payload(payload_obj)
            payload = _refresh_execute_intake(payload)
            warnings = []
            proposed_rows = [
                row for row in list((_as_dict(payload.get("artefacts"))).get("proposed") or [])
                if isinstance(row, dict) or str(row or "").strip()
            ]
            if not proposed_rows:
                warnings.append(
                    "No artefacts were proposed. Ask EXECUTE for explicit artefacts.proposed with kind/title/notes."
                )
            if _execute_has_requirements(payload):
                warnings.append("Execute intake identified missing inputs in artefacts.requirements.")

            persist_derax_payload(work_item=work_item, payload=payload, user=request.user, chat=None)
            execute_text = _readable_derax_text(json.dumps(payload, ensure_ascii=True, indent=2))
            work_item.append_activity(
                actor=request.user,
                action="execute_llm_turn",
                notes="EXECUTE intake turn recorded.",
            )
            if warnings:
                work_item.append_activity(
                    actor="system",
                    action="execute_artefact_generation_warning",
                    notes="; ".join([str(w) for w in warnings if str(w).strip()])[:500],
                )
            if _is_ajax(request):
                return JsonResponse({"ok": True, "latest_define_assistant_text": execute_text})
            if warnings:
                messages.warning(request, "EXECUTE intake captured: " + "; ".join(warnings))
            else:
                messages.success(request, "EXECUTE intake captured. Use Generate artefacts when ready.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "generate_execute_artefacts":
            latest_execute_payload = _latest_payload_from_runs(work_item, WorkItem.PHASE_EXECUTE)
            if not isinstance(latest_execute_payload, dict):
                err_text = "Run EXECUTE intake first before generating artefacts."
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": err_text}, status=400)
                messages.error(request, err_text)
                return redirect("projects:derax_project_home", project_id=project.id)
            payload = _refresh_execute_intake(latest_execute_payload)
            doc_inputs = _parse_execute_doc_inputs(str(request.POST.get("execute_doc_inputs_json") or ""))
            skip_raw = str(request.POST.get("execute_skip_rows") or "").strip()
            skip_indexes = set()
            if skip_raw:
                for token in skip_raw.split(","):
                    token = str(token or "").strip()
                    if not token:
                        continue
                    try:
                        idx = int(token)
                    except Exception:
                        continue
                    if idx >= 0:
                        skip_indexes.add(idx)
            artefacts = _as_dict(payload.get("artefacts"))
            proposed_rows = list(artefacts.get("proposed") or [])
            kept_rows_with_idx = [(idx, row) for idx, row in enumerate(proposed_rows) if idx not in skip_indexes]
            kept_rows = []
            for row_idx, row in kept_rows_with_idx:
                row_dict = _as_dict(row)
                doc_input = str(doc_inputs.get(row_idx) or "").strip()
                if doc_input:
                    notes = str(row_dict.get("notes") or "").strip()
                    row_dict["notes"] = (notes + "\n" if notes else "") + "Input provided: " + doc_input
                kept_rows.append(row_dict)
            artefacts["proposed"] = kept_rows
            requirements_map = _execute_requirements_map(payload)
            kept_kinds = set()
            for row in kept_rows:
                row_kind = str(_as_dict(row).get("kind") or "").strip()
                if row_kind:
                    kept_kinds.add(row_kind)
            filtered_requirements = {
                key: value
                for key, value in requirements_map.items()
                if key in kept_kinds
            }
            for row_idx, row in kept_rows_with_idx:
                row_kind = str(_as_dict(row).get("kind") or "").strip()
                if not row_kind:
                    continue
                if str(doc_inputs.get(row_idx) or "").strip():
                    filtered_requirements[row_kind] = []
            artefacts["requirements"] = filtered_requirements
            payload["artefacts"] = artefacts
            payload = _refresh_execute_intake(payload)
            proposed_rows = [
                row for row in list((_as_dict(payload.get("artefacts"))).get("proposed") or [])
                if isinstance(row, dict) or str(row or "").strip()
            ]
            if not proposed_rows:
                err_text = "No artefacts proposed after skipping. Keep at least one document in the delivery package."
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": err_text}, status=400)
                messages.error(request, err_text)
                return redirect("projects:derax_project_home", project_id=project.id)
            if _execute_has_missing_proposed_kind(payload):
                err_text = "EXECUTE intake incomplete. Each proposed document must include a kind."
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": err_text}, status=400)
                messages.error(request, err_text)
                return redirect("projects:derax_project_home", project_id=project.id)

            intake_map = _execute_intake_map(payload)
            ready_rows = []
            for idx, row in enumerate(list((_as_dict(payload.get("artefacts"))).get("proposed") or [])):
                intake = _as_dict(intake_map.get(str(idx)))
                if str(intake.get("status") or "").strip().upper() == "READY":
                    ready_rows.append(row)
            if not ready_rows:
                err_text = "No READY documents to generate. Fill missing inputs or skip unresolved documents."
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": err_text}, status=400)
                messages.error(request, err_text)
                return redirect("projects:derax_project_home", project_id=project.id)

            warnings = []
            try:
                payload_for_generation = copy.deepcopy(payload)
                artefacts_for_generation = _as_dict(payload_for_generation.get("artefacts"))
                artefacts_for_generation["proposed"] = ready_rows
                payload_for_generation["artefacts"] = artefacts_for_generation
                gen_result = generate_artefacts_from_execute_payload(
                    project_id=project.id,
                    chat_id=int(work_item.id),
                    turn_id=timezone.now().strftime("%Y%m%dT%H%M%S"),
                    payload=payload_for_generation,
                    user_id=getattr(request.user, "id", None),
                )
                warnings = list(gen_result.get("warnings") or [])
                generated_rows = list((_as_dict(payload_for_generation.get("artefacts"))).get("generated") or [])
                artefacts_final = _as_dict(payload.get("artefacts"))
                artefacts_final["generated"] = generated_rows
                payload["artefacts"] = artefacts_final
            except Exception as exc:
                err_text = f"Execute artefact generation failed: {exc}"
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": err_text}, status=500)
                messages.error(request, err_text)
                return redirect("projects:derax_project_home", project_id=project.id)

            persist_derax_payload(work_item=work_item, payload=payload, user=request.user, chat=None)
            execute_text = _readable_derax_text(json.dumps(payload, ensure_ascii=True, indent=2))
            work_item.append_activity(
                actor=request.user,
                action="execute_generate_artefacts",
                notes="EXECUTE artefacts generated from latest intake payload.",
            )
            if warnings:
                work_item.append_activity(
                    actor="system",
                    action="execute_artefact_generation_warning",
                    notes="; ".join([str(w) for w in warnings if str(w).strip()])[:500],
                )
            if _is_ajax(request):
                return JsonResponse({"ok": True, "latest_define_assistant_text": execute_text})
            if warnings:
                messages.warning(request, "EXECUTE artefacts generated with warnings: " + "; ".join(warnings))
            else:
                messages.success(request, "EXECUTE artefacts generated.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "execute_recheck_doc_intake":
            latest_execute_payload = _latest_payload_from_runs(work_item, WorkItem.PHASE_EXECUTE)
            if not isinstance(latest_execute_payload, dict):
                err_text = "Run EXECUTE intake first."
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": err_text}, status=400)
                messages.error(request, err_text)
                return redirect("projects:derax_project_home", project_id=project.id)
            try:
                doc_idx = int(str(request.POST.get("doc_idx") or "").strip())
            except Exception:
                doc_idx = -1
            doc_input = str(request.POST.get("doc_input") or "").strip()
            payload = _refresh_execute_intake(latest_execute_payload)
            artefacts = _as_dict(payload.get("artefacts"))
            proposed = list(artefacts.get("proposed") or [])
            if doc_idx < 0 or doc_idx >= len(proposed):
                err_text = "Invalid document index for intake re-check."
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": err_text}, status=400)
                messages.error(request, err_text)
                return redirect("projects:derax_project_home", project_id=project.id)
            row_dict = _as_dict(proposed[doc_idx])
            row_kind = str(row_dict.get("kind") or "").strip()
            if doc_input:
                notes = str(row_dict.get("notes") or "").strip()
                row_dict["notes"] = (notes + "\n" if notes else "") + "Input provided: " + doc_input
                proposed[doc_idx] = row_dict
                artefacts["proposed"] = proposed
                req_map = _execute_requirements_map(payload)
                if row_kind:
                    req_map[row_kind] = []
                artefacts["requirements"] = req_map
                payload["artefacts"] = artefacts
            payload = _refresh_execute_intake(payload)
            persist_derax_payload(work_item=work_item, payload=payload, user=request.user, chat=None)
            if _is_ajax(request):
                return JsonResponse({"ok": True, "latest_define_assistant_text": _readable_derax_text(json.dumps(payload, ensure_ascii=True, indent=2))})
            messages.success(request, "Document intake re-checked.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "execute_skip_doc":
            latest_execute_payload = _latest_payload_from_runs(work_item, WorkItem.PHASE_EXECUTE)
            if not isinstance(latest_execute_payload, dict):
                err_text = "Run EXECUTE intake first."
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": err_text}, status=400)
                messages.error(request, err_text)
                return redirect("projects:derax_project_home", project_id=project.id)
            try:
                doc_idx = int(str(request.POST.get("doc_idx") or "").strip())
            except Exception:
                doc_idx = -1
            payload = _refresh_execute_intake(latest_execute_payload)
            artefacts = _as_dict(payload.get("artefacts"))
            proposed = list(artefacts.get("proposed") or [])
            if doc_idx < 0 or doc_idx >= len(proposed):
                err_text = "Invalid document index for skip."
                if _is_ajax(request):
                    return JsonResponse({"ok": False, "error": err_text}, status=400)
                messages.error(request, err_text)
                return redirect("projects:derax_project_home", project_id=project.id)
            removed_row = _as_dict(proposed[doc_idx])
            removed_kind = str(removed_row.get("kind") or "").strip()
            kept_rows = [row for idx, row in enumerate(proposed) if idx != doc_idx]
            artefacts["proposed"] = kept_rows
            req_map = _execute_requirements_map(payload)
            if removed_kind:
                req_map = {k: v for k, v in req_map.items() if str(k).strip() != removed_kind}
            artefacts["requirements"] = req_map
            payload["artefacts"] = artefacts
            payload = _refresh_execute_intake(payload)
            persist_derax_payload(work_item=work_item, payload=payload, user=request.user, chat=None)
            work_item.append_activity(
                actor=request.user,
                action="execute_skip_doc",
                notes=f"Skipped execute document at index {doc_idx}.",
            )
            if _is_ajax(request):
                return JsonResponse({"ok": True, "latest_define_assistant_text": _readable_derax_text(json.dumps(payload, ensure_ascii=True, indent=2))})
            messages.success(request, "Document skipped from delivery package.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "lock_define_and_explore":
            intent = str(request.POST.get("lock_seed_text") or "").strip()
            if not intent:
                intent = str(work_item.intent_raw or "").strip()
            if not intent:
                intent = _extract_end_in_mind(_latest_define_assistant_text(work_item))
            if not intent:
                messages.error(request, "Set intent first before locking DEFINE.")
                return redirect("projects:derax_project_home", project_id=project.id)
            if str(work_item.active_phase or "").strip().upper() != WorkItem.PHASE_DEFINE:
                messages.error(request, "DEFINE can only be locked while active phase is DEFINE.")
                return redirect("projects:derax_project_home", project_id=project.id)
            try:
                work_item.append_seed_revision(
                    seed_text=intent,
                    created_by=request.user,
                    reason="DEFINE_LOCKED",
                )
                work_item.set_phase(WorkItem.PHASE_EXPLORE)
            except Exception as exc:
                messages.error(request, str(exc))
                return redirect("projects:derax_project_home", project_id=project.id)
            if str(work_item.intent_raw or "").strip() != intent:
                work_item.intent_raw = intent
                work_item.save(update_fields=["intent_raw", "updated_at"])
            messages.success(request, "DEFINE locked to history. Phase moved to EXPLORE.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "lock_explore_and_refine":
            destination_text = str(work_item.intent_raw or "").strip()
            if not destination_text:
                messages.error(request, "Set destination text before locking EXPLORE.")
                return redirect("projects:derax_project_home", project_id=project.id)
            if str(work_item.active_phase or "").strip().upper() != WorkItem.PHASE_EXPLORE:
                messages.error(request, "EXPLORE can only be locked while active phase is EXPLORE.")
                return redirect("projects:derax_project_home", project_id=project.id)
            try:
                work_item.append_seed_revision(
                    seed_text=destination_text,
                    created_by=request.user,
                    reason="EXPLORE_LOCKED",
                )
                work_item.set_phase(WorkItem.PHASE_REFINE)
            except Exception as exc:
                messages.error(request, str(exc))
                return redirect("projects:derax_project_home", project_id=project.id)
            messages.success(request, "EXPLORE locked to history. Phase moved to REFINE.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "return_to_define":
            current = str(work_item.active_phase or "").strip().upper()
            if current != WorkItem.PHASE_EXPLORE:
                messages.error(request, "Return to DEFINE is available only from EXPLORE.")
                return redirect("projects:derax_project_home", project_id=project.id)
            work_item.active_phase = WorkItem.PHASE_DEFINE
            work_item.save(update_fields=["active_phase", "updated_at"])
            work_item.append_activity(
                actor=request.user,
                action="phase_changed",
                notes="EXPLORE -> DEFINE (manual return)",
            )
            messages.success(request, "Returned to DEFINE. Full audit trail is preserved.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "reenter_phase":
            target = str(request.POST.get("target_phase") or "").strip().upper()
            allowed_reenter_targets = {
                WorkItem.PHASE_DEFINE,
                WorkItem.PHASE_EXPLORE,
                WorkItem.PHASE_REFINE,
                WorkItem.PHASE_APPROVE,
                WorkItem.PHASE_EXECUTE,
                WorkItem.PHASE_COMPLETE,
            }
            if target not in allowed_reenter_targets:
                messages.error(request, "Invalid phase target.")
                return redirect("projects:derax_project_home", project_id=project.id)
            before = str(work_item.active_phase or "").strip().upper() or WorkItem.PHASE_DEFINE
            if before == target:
                messages.info(request, f"Already in {target}.")
                return redirect("projects:derax_project_home", project_id=project.id)
            work_item.active_phase = target
            work_item.save(update_fields=["active_phase", "updated_at"])
            work_item.append_activity(
                actor=request.user,
                action="phase_changed",
                notes=f"{before} -> {target} (manual re-enter)",
            )
            messages.success(request, f"Re-entered {target}.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "compile_derax_cko":
            try:
                doc = persist_compiled_cko(work_item, user=request.user)
            except Exception as exc:
                messages.error(request, f"CKO compile failed: {exc}")
                return redirect("projects:derax_project_home", project_id=project.id)
            messages.success(request, f"CKO artefact saved: {doc.original_name or doc.title}")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "generate_derax_audit":
            try:
                doc = persist_derax_project_audit(work_item, user=request.user)
            except Exception as exc:
                messages.error(request, f"DERAX audit export failed: {exc}")
                return redirect("projects:derax_project_home", project_id=project.id)
            messages.success(request, f"DERAX audit file saved: {doc.original_name or doc.title}")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "export_latest_derax_draft":
            phase = str(work_item.active_phase or WorkItem.PHASE_DEFINE).strip().upper()
            payload = _latest_payload_for_phase(work_item, phase)
            if not payload:
                payload = empty_payload(phase)
                payload["intent"]["destination"] = str(work_item.intent_raw or "").strip()
            body = _build_editable_markdown(payload, phase=phase)
            stamp = timezone.now().strftime("%Y%m%dT%H%M%S")
            stem = _safe_stem(work_item.project.name)
            filename = f"{stem}-DERAX-{phase}-Editable-{stamp}.odt"
            rel_name = f"derax/{int(work_item.id)}/{filename}"
            odt_bytes = _odt_bytes_from_text(body)
            doc = ProjectDocument(
                project=work_item.project,
                title=f"DERAX {phase} Editable Draft"[:200],
                original_name=filename[:255],
                content_type="application/vnd.oasis.opendocument.text",
                size_bytes=len(odt_bytes),
                uploaded_by=request.user,
            )
            doc.file.save(rel_name, ContentFile(odt_bytes), save=False)
            doc.save()
            messages.success(request, f"Editable DERAX draft saved: {doc.original_name or doc.title}")
            edit_url = reverse("projects:project_document_collabora_edit", args=[project.id, doc.id])
            back_url = reverse("projects:derax_project_home", args=[project.id])
            return redirect(f"{edit_url}?next={back_url}")

        if action in {"export_refine_input_draft", "export_refine_response_draft"}:
            body = str(request.POST.get("candidate_text") or "").strip()
            if not body:
                body = _latest_refine_response_text(work_item)
            if not body:
                body = str(work_item.intent_raw or "").strip()
            if not body:
                messages.error(request, "No REFINE response available to export.")
                return redirect("projects:derax_project_home", project_id=project.id)
            stamp = timezone.now().strftime("%Y%m%dT%H%M%S")
            stem = _safe_stem(work_item.project.name)
            filename = f"{stem}-DERAX-REFINE-Response-{stamp}.odt"
            rel_name = f"derax/{int(work_item.id)}/{filename}"
            odt_bytes = _odt_bytes_from_text(body)
            doc = ProjectDocument(
                project=work_item.project,
                title="DERAX REFINE Response Draft"[:200],
                original_name=filename[:255],
                content_type="application/vnd.oasis.opendocument.text",
                size_bytes=len(odt_bytes),
                uploaded_by=request.user,
            )
            doc.file.save(rel_name, ContentFile(odt_bytes), save=False)
            doc.save()
            messages.success(request, f"REFINE response draft saved: {doc.original_name or doc.title}")
            edit_url = reverse("projects:project_document_collabora_edit", args=[project.id, doc.id])
            back_url = reverse("projects:derax_project_home", args=[project.id])
            return redirect(f"{edit_url}?next={back_url}")

        if action == "import_derax_from_document":
            import_target = str(request.POST.get("import_target_phase") or work_item.active_phase or WorkItem.PHASE_DEFINE).strip().upper()
            if import_target not in {
                WorkItem.PHASE_DEFINE,
                WorkItem.PHASE_EXPLORE,
                WorkItem.PHASE_REFINE,
                WorkItem.PHASE_APPROVE,
                WorkItem.PHASE_EXECUTE,
            }:
                import_target = WorkItem.PHASE_DEFINE
            doc_id_raw = str(request.POST.get("import_doc_id") or "").strip()
            if not doc_id_raw.isdigit():
                messages.error(request, "Select a document to import.")
                return redirect("projects:derax_project_home", project_id=project.id)
            doc = ProjectDocument.objects.filter(id=int(doc_id_raw), project=project).first()
            if doc is None:
                messages.error(request, "Document not found.")
                return redirect("projects:derax_project_home", project_id=project.id)
            try:
                doc.file.open("rb")
                try:
                    raw_bytes = doc.file.read()
                finally:
                    doc.file.close()
            except Exception as exc:
                messages.error(request, f"Failed to read document: {exc}")
                return redirect("projects:derax_project_home", project_id=project.id)
            markdown_text = _extract_document_text(doc, raw_bytes)
            payload = _try_parse_json_payload(markdown_text)
            if payload is None:
                refine_text = _extract_refine_input_from_markdown(markdown_text)
                if refine_text and import_target == WorkItem.PHASE_REFINE:
                    work_item.intent_raw = refine_text
                    work_item.save(update_fields=["intent_raw", "updated_at"])
                    work_item.append_activity(
                        actor=request.user,
                        action="derax_refine_input_imported",
                        notes=f"doc_id={doc.id}",
                    )
                    messages.success(request, f"Imported REFINE input from document: {doc.original_name or doc.title}")
                    return redirect("projects:derax_project_home", project_id=project.id)
                payload = _parse_editable_markdown(markdown_text)

            payload.setdefault("meta", {})
            payload["meta"]["phase"] = import_target
            payload["meta"]["source_chat_id"] = ""
            payload["meta"]["source_turn_id"] = f"doc:{doc.id}"
            payload["meta"]["tko_id"] = f"tko_derax_import_{work_item.id}"
            ok_schema, schema_errors = validate_structural(payload)
            ok_phase, phase_errors = check_required_nonempty(payload)
            if not ok_schema or not ok_phase:
                errors = list(schema_errors or []) + list(phase_errors or [])
                messages.error(request, "Import failed validation: " + "; ".join([str(e) for e in errors if str(e).strip()]))
                return redirect("projects:derax_project_home", project_id=project.id)
            phase = import_target
            persist_derax_payload(work_item=work_item, payload=payload, user=request.user, chat=None)
            now_iso = timezone.now().isoformat()
            entry_user = {"role": "user", "text": f"Imported from document {doc.id}: {doc.original_name or doc.title}", "timestamp": now_iso}
            entry_assistant = {"role": "assistant", "text": json.dumps(payload, ensure_ascii=True, indent=2), "timestamp": now_iso}
            if phase == WorkItem.PHASE_EXPLORE:
                history = [h for h in list(work_item.derax_explore_history or []) if isinstance(h, dict)]
                history.extend([entry_user, entry_assistant])
                work_item.derax_explore_history = history[-40:]
                work_item.intent_raw = _extract_end_in_mind(entry_assistant["text"])
                work_item.save(update_fields=["derax_explore_history", "intent_raw", "updated_at"])
            elif phase in {WorkItem.PHASE_REFINE, WorkItem.PHASE_APPROVE, WorkItem.PHASE_EXECUTE}:
                work_item.intent_raw = _extract_end_in_mind(entry_assistant["text"])
                work_item.save(update_fields=["intent_raw", "updated_at"])
            else:
                history = [h for h in list(work_item.derax_define_history or []) if isinstance(h, dict)]
                history.extend([entry_user, entry_assistant])
                work_item.derax_define_history = history[-40:]
                work_item.intent_raw = _extract_end_in_mind(entry_assistant["text"])
                work_item.save(update_fields=["derax_define_history", "intent_raw", "updated_at"])
            work_item.append_activity(
                actor=request.user,
                action="derax_document_imported",
                notes=f"doc_id={doc.id}; target_phase={phase}",
            )
            messages.success(request, f"Imported into {phase}: {doc.original_name or doc.title}")
            return redirect("projects:derax_project_home", project_id=project.id)

    seed_history = []
    for item in reversed(list(work_item.seed_log or [])):
        if not isinstance(item, dict):
            continue
        seed_history.append(
            {
                "revision": int(item.get("revision") or 0),
                "status": str(item.get("status") or ""),
                "created_at": str(item.get("created_at") or ""),
                "reason": str(item.get("reason") or ""),
                "seed_text": str(item.get("seed_text") or ""),
            }
        )

    active_phase = str(work_item.active_phase or "").strip().upper() or WorkItem.PHASE_DEFINE
    is_define = active_phase == WorkItem.PHASE_DEFINE
    is_explore = active_phase == WorkItem.PHASE_EXPLORE
    is_refine = active_phase == WorkItem.PHASE_REFINE
    is_approve = active_phase == WorkItem.PHASE_APPROVE
    is_execute = active_phase == WorkItem.PHASE_EXECUTE
    define_history_rows = _build_phase_history_rows(list(work_item.derax_define_history or []))
    explore_history_rows = _build_phase_history_rows(list(work_item.derax_explore_history or []))
    refine_history_rows = _build_refine_history_rows(work_item)
    approve_history_rows = _build_run_history_rows(work_item, WorkItem.PHASE_APPROVE)
    execute_history_rows = _build_run_history_rows(work_item, WorkItem.PHASE_EXECUTE)
    if is_define:
        phase_history = define_history_rows
    elif is_explore:
        phase_history = explore_history_rows
    elif is_refine:
        phase_history = refine_history_rows
    elif is_approve:
        phase_history = approve_history_rows
    else:
        phase_history = execute_history_rows

    if is_define:
        latest_phase_raw = _latest_define_assistant_text(work_item)
        latest_phase_response = _readable_derax_text(latest_phase_raw)
    elif is_explore:
        latest_phase_raw = _latest_explore_assistant_text(work_item)
        latest_phase_response = _readable_derax_text(latest_phase_raw)
    else:
        latest_phase_payload_any = _latest_payload_from_runs(work_item, active_phase)
        latest_phase_raw = json.dumps(latest_phase_payload_any, ensure_ascii=True, indent=2) if latest_phase_payload_any else ""
        latest_phase_response = _readable_derax_text(latest_phase_raw)
    latest_phase_payload = _parse_history_payload(latest_phase_raw) or {}
    latest_phase_payload_json = json.dumps(latest_phase_payload, ensure_ascii=True, indent=2) if latest_phase_payload else ""
    stage_execute_proposed_text = _execute_proposed_to_multiline(
        list((latest_phase_payload.get("artefacts") or {}).get("proposed") or [])
    )
    define_latest_payload = _latest_payload_for_phase(work_item, WorkItem.PHASE_DEFINE)
    explore_latest_payload = _latest_payload_for_phase(work_item, WorkItem.PHASE_EXPLORE)
    approve_latest_payload = _latest_payload_from_runs(work_item, WorkItem.PHASE_APPROVE)
    refine_latest_payload = _latest_payload_from_runs(work_item, WorkItem.PHASE_REFINE)
    execute_latest_payload = _latest_payload_from_runs(work_item, WorkItem.PHASE_EXECUTE)
    execute_payload_for_ui = _sanitise_execute_payload(execute_latest_payload) if isinstance(execute_latest_payload, dict) else {}
    execute_missing_requirement_kinds = _execute_missing_requirement_kinds(execute_payload_for_ui)
    execute_unresolved_requirement_kinds = _execute_unresolved_requirement_kinds(execute_payload_for_ui)
    execute_requirements_pending = bool(execute_unresolved_requirement_kinds)
    execute_requirements_map = _execute_requirements_map(execute_payload_for_ui)
    execute_intake_map = _execute_intake_map(execute_payload_for_ui)
    execute_has_proposed = bool(list((_as_dict(execute_payload_for_ui.get("artefacts"))).get("proposed") or []))
    execute_ready_for_generation = bool(
        execute_has_proposed
        and not execute_missing_requirement_kinds
        and not execute_requirements_pending
    )
    execute_proposed_rows_ui = []
    execute_ready_docs_count = 0
    execute_pending_docs_count = 0
    for idx, row in enumerate(list((_as_dict(execute_payload_for_ui.get("artefacts"))).get("proposed") or [])):
        row_dict = _as_dict(row)
        row_kind = str(row_dict.get("kind") or "").strip()
        row_title = str(row_dict.get("title") or "").strip()
        row_notes = str(row_dict.get("notes") or "").strip()
        intake_row = _as_dict(execute_intake_map.get(str(idx)))
        row_status = str(intake_row.get("status") or "").strip().upper() or "MISSING_INPUTS"
        req_rows = list(intake_row.get("requirements") or [])
        if row_status == "READY":
            execute_ready_docs_count += 1
        else:
            execute_pending_docs_count += 1
        execute_proposed_rows_ui.append(
            {
                "idx": idx,
                "kind": row_kind,
                "title": row_title,
                "notes": row_notes,
                "why_text": _execute_doc_why_text(row_dict),
                "strawman_text": _execute_doc_strawman_text(row_dict),
                "topics_list": _execute_doc_topics(row_dict),
                "status": row_status,
                "requirements": req_rows,
            }
        )
    execute_ready_for_generation = bool(execute_ready_docs_count > 0)
    execute_export_caps = execute_export_capabilities()
    execute_export_missing = [name for name, ok in execute_export_caps.items() if not ok]
    latest_refine_response = _latest_refine_response_text(work_item)
    approve_latest_text = _readable_derax_text(json.dumps(approve_latest_payload, ensure_ascii=True, indent=2)) if approve_latest_payload else ""
    explore_vm = _explore_view_model(explore_latest_payload)
    refine_vm = _refine_view_model(refine_latest_payload)
    explore_latest_text = _readable_derax_text(_latest_explore_assistant_text(work_item))
    phase_input_text = str(work_item.intent_raw or "").strip()
    define_locked_seed_text = _latest_seed_by_reason(work_item, "DEFINE_LOCKED")
    if is_explore and not phase_input_text:
        phase_input_text = define_locked_seed_text
    explore_locked_seed_text = _latest_seed_by_reason(work_item, "EXPLORE_LOCKED")
    refine_input_text = str(work_item.intent_raw or "").strip()
    if is_refine:
        if (
            explore_latest_text
            and (not refine_input_text or refine_input_text == define_locked_seed_text)
        ):
            refine_input_text = explore_latest_text
        if (
            explore_locked_seed_text
            and (not refine_input_text or refine_input_text == define_locked_seed_text)
        ):
            refine_input_text = explore_locked_seed_text
        if not refine_input_text:
            refine_input_text = explore_latest_text
        if not refine_input_text:
            refine_input_text = define_locked_seed_text
    if not latest_phase_response and phase_input_text:
        latest_phase_response = phase_input_text
    refine_payload_for_edit = refine_latest_payload if isinstance(refine_latest_payload, dict) else empty_payload(WorkItem.PHASE_REFINE)
    refine_editor_canonical_summary = str(refine_payload_for_edit.get("canonical_summary") or "").strip()
    refine_editor_destination = _str_from_payload(refine_payload_for_edit, ("intent", "destination"), ("core", "end_in_mind"))
    refine_editor_success_criteria = _list_to_multiline(_list_from_payload(refine_payload_for_edit, ("intent", "success_criteria"), ("core", "destination_conditions")))
    refine_editor_constraints = _list_to_multiline(_list_from_payload(refine_payload_for_edit, ("intent", "constraints"), ("core", "assumptions")))
    refine_editor_non_goals = _list_to_multiline(_list_from_payload(refine_payload_for_edit, ("intent", "non_goals"), ("core", "non_goals")))
    refine_editor_assumptions = _list_to_multiline(_list_from_payload(refine_payload_for_edit, ("intent", "assumptions"), ("core", "assumptions")))
    refine_editor_open_questions = _list_to_multiline(_list_from_payload(refine_payload_for_edit, ("intent", "open_questions"), ("core", "ambiguities")))
    refine_editor_adjacent_ideas = _list_to_multiline(_list_from_payload(refine_payload_for_edit, ("explore", "adjacent_ideas"), ("core", "adjacent_angles")))
    refine_editor_risks = _list_to_multiline(_list_from_payload(refine_payload_for_edit, ("explore", "risks"), ("core", "risks")))
    refine_editor_tradeoffs = _list_to_multiline(_list_from_payload(refine_payload_for_edit, ("explore", "tradeoffs"), ("core", "scope_changes")))
    refine_editor_reframes = _list_to_multiline(_list_from_payload(refine_payload_for_edit, ("explore", "reframes"), ("core", "ambiguities")))
    parked_items = []
    for item in list((refine_payload_for_edit.get("parked_for_later") or {}).get("items") or []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        detail = str(item.get("detail") or "").strip()
        if title and detail:
            parked_items.append(f"{title}: {detail}")
        elif title:
            parked_items.append(title)
        elif detail:
            parked_items.append(detail)
    refine_editor_parked_items = _list_to_multiline(parked_items)

    active_contract = resolve_phase_contract(
        ContractContext(
            user=request.user,
            project=project,
            work_item=work_item,
            active_phase=str(work_item.active_phase or ""),
            user_text="",
            is_derax=True,
            include_envelope=False,
            strict_json=False,
        )
    )
    contract_phase_options = list(WorkItem.ALLOWED_PHASES)
    contract_effective_by_phase = {}
    contract_default_by_phase = {}
    for phase_name in contract_phase_options:
        contract_effective_by_phase[phase_name], _source = _phase_contract_effective_text(
            user=request.user,
            phase_name=phase_name,
            project_id=project.id,
        )
        contract_default_by_phase[phase_name] = _phase_contract_default_text(phase_name)
    active_phase_contract_raw = str(contract_effective_by_phase.get(active_phase) or "").strip()
    phase_status_map = _phase_status_map(work_item)

    derax_audit_history_all = _merged_derax_audit_history(work_item)
    derax_audit_history_current = [
        row for row in derax_audit_history_all
        if str(row.get("phase") or "").strip().upper() == active_phase
    ]

    files_sort = str(request.GET.get("files_sort") or "updated").strip().lower()
    files_dir = str(request.GET.get("files_dir") or "desc").strip().lower()
    files_sort_map = {
        "updated": "updated_at",
        "name": "original_name",
        "size": "size_bytes",
    }
    files_order_field = files_sort_map.get(files_sort, "updated_at")
    if files_dir == "asc":
        files_order = files_order_field
        files_secondary = "id"
    else:
        files_order = f"-{files_order_field}"
        files_secondary = "-id"
    all_project_docs_qs = list(
        ProjectDocument.objects
        .filter(project=project, is_archived=False)
        .order_by(files_order, files_secondary)[:100]
    )
    all_project_docs = [
        {
            "doc": doc,
            "display_name": _display_file_name(str(doc.original_name or doc.title or "")),
        }
        for doc in all_project_docs_qs
    ]

    return render(
        request,
        "projects/derax_home.html",
        {
            "project": project,
            "work_item": work_item,
            "seed_history": seed_history,
            "active_phase_upper": active_phase,
            "is_define_phase": is_define,
            "is_explore_phase": is_explore,
            "is_refine_phase": is_refine,
            "is_approve_phase": is_approve,
            "is_execute_phase": is_execute,
            "phase_history": phase_history,
            "define_history_rows": define_history_rows,
            "explore_history_rows": explore_history_rows,
            "refine_history_rows": refine_history_rows,
            "approve_history_rows": approve_history_rows,
            "execute_history_rows": execute_history_rows,
            "latest_phase_response": latest_phase_response,
            "latest_phase_payload": latest_phase_payload,
            "latest_phase_payload_json": latest_phase_payload_json,
            "stage_execute_proposed_text": stage_execute_proposed_text,
            "latest_refine_response": latest_refine_response,
            "approve_latest_text": approve_latest_text,
            "define_latest_payload": define_latest_payload,
            "explore_latest_payload": explore_latest_payload,
            "approve_latest_payload": approve_latest_payload,
            "explore_vm": explore_vm,
            "refine_vm": refine_vm,
            "explore_latest_text": explore_latest_text,
            "explore_locked_seed_text": explore_locked_seed_text,
            "define_latest_payload_json": json.dumps(define_latest_payload, ensure_ascii=True, indent=2) if define_latest_payload else "",
            "explore_latest_payload_json": json.dumps(explore_latest_payload, ensure_ascii=True, indent=2) if explore_latest_payload else "",
            "refine_latest_payload_json": json.dumps(refine_latest_payload, ensure_ascii=True, indent=2) if refine_latest_payload else "",
            "approve_latest_payload_json": json.dumps(approve_latest_payload, ensure_ascii=True, indent=2) if approve_latest_payload else "",
            "execute_latest_payload_json": json.dumps(execute_latest_payload, ensure_ascii=True, indent=2) if execute_latest_payload else "",
            "execute_export_missing": execute_export_missing,
            "execute_requirements_pending": execute_requirements_pending,
            "execute_missing_requirement_kinds": execute_missing_requirement_kinds,
            "execute_unresolved_requirement_kinds": execute_unresolved_requirement_kinds,
            "execute_requirements_map": execute_requirements_map,
            "execute_intake_map": execute_intake_map,
            "execute_has_proposed": execute_has_proposed,
            "execute_ready_for_generation": execute_ready_for_generation,
            "execute_proposed_rows_ui": execute_proposed_rows_ui,
            "execute_ready_docs_count": execute_ready_docs_count,
            "execute_pending_docs_count": execute_pending_docs_count,
            "phase_input_text": phase_input_text,
            "refine_input_text": refine_input_text,
            "refine_editor_canonical_summary": refine_editor_canonical_summary,
            "refine_editor_destination": refine_editor_destination,
            "refine_editor_success_criteria": refine_editor_success_criteria,
            "refine_editor_constraints": refine_editor_constraints,
            "refine_editor_non_goals": refine_editor_non_goals,
            "refine_editor_assumptions": refine_editor_assumptions,
            "refine_editor_open_questions": refine_editor_open_questions,
            "refine_editor_adjacent_ideas": refine_editor_adjacent_ideas,
            "refine_editor_risks": refine_editor_risks,
            "refine_editor_tradeoffs": refine_editor_tradeoffs,
            "refine_editor_reframes": refine_editor_reframes,
            "refine_editor_parked_items": refine_editor_parked_items,
            "define_help_text": (
                "Describe the outcome you want from this DERAX process. "
                "State what good looks like. "
                "Write it in your own words so the LLM can help define and refine it."
            ),
            "show_contract_debug": bool(settings.DEBUG),
            "active_phase_contract": active_contract,
            "active_phase_contract_raw": active_phase_contract_raw,
            "contract_phase_options": contract_phase_options,
            "contract_effective_by_phase": contract_effective_by_phase,
            "contract_default_by_phase": contract_default_by_phase,
            "can_edit_phase_contracts": can_edit_phase_contracts,
            "phase_route_map": _phase_route_map(work_item),
            "define_phase_locked": phase_status_map.get(WorkItem.PHASE_DEFINE) == "LOCKED",
            "explore_phase_locked": phase_status_map.get(WorkItem.PHASE_EXPLORE) == "LOCKED",
            "refine_phase_locked": phase_status_map.get(WorkItem.PHASE_REFINE) == "LOCKED",
            "approve_phase_locked": phase_status_map.get(WorkItem.PHASE_APPROVE) == "LOCKED",
            "execute_phase_locked": phase_status_map.get(WorkItem.PHASE_EXECUTE) == "LOCKED",
            "derax_audit_history": derax_audit_history_all,
            "derax_audit_history_current": derax_audit_history_current,
            "derax_import_docs": list(
                ProjectDocument.objects
                .filter(project=project, is_archived=False, original_name__icontains="-DERAX-")
                .filter(
                    Q(original_name__iendswith=".txt")
                    | Q(original_name__iendswith=".md")
                    | Q(original_name__iendswith=".odt")
                )
                .order_by("-updated_at", "-id")[:50]
            ),
            "all_project_docs": all_project_docs,
            "can_archive_project_docs": can_archive_project_docs,
            "can_hard_delete_project_docs": can_hard_delete_project_docs,
            "files_sort": files_sort,
            "files_dir": files_dir,
        },
    )
