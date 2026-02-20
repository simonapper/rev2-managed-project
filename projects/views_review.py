from __future__ import annotations

import json
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from chats.services.llm import generate_panes
from chats.models import ChatWorkspace
from chats.services.turns import build_chat_turn_context
from projects.models import (
    ProjectAnchor,
    ProjectAnchorAudit,
    ProjectReviewChat,
    ProjectReviewStageChat,
    ProjectCKO,
    UserProjectPrefs,
)
from projects.services_project_membership import accessible_projects_qs
from projects.services.artefact_render import render_artefact_html
from projects.services_artefacts import (
    build_cko_seed_text,
    get_pdo_schema_text,
    merge_execute_payload,
    normalise_pdo_payload,
    seed_execute_from_route,
)
from projects.services_execute import seed_execute_from_route as ensure_execute_from_route, reseed_execute_from_route
from projects.services_text_normalise import normalise_sections
from projects.services_review_chat import get_or_create_review_chat, get_or_create_review_stage_chat
from projects.services_execute_validator import build_execute_conference_seed, build_execute_stage_seed

MARKERS = [
    ("INTENT", "Intent", "CKO (text)"),
    ("ROUTE", "Route", "PDO (JSON/text)"),
    ("EXECUTE", "Execute", "Execution state (text/JSON)"),
    ("COMPLETE", "Complete", "Completion report (text)"),
]
ANCHOR_KIND = {
    "INTENT": "CKO",
    "ROUTE": "",
    "EXECUTE": "",
    "COMPLETE": "",
}
EXECUTE_STAGE_STATUS_CHOICES = (
    "NOT_STARTED",
    "IN_PROGRESS",
    "BLOCKED",
    "AT_RISK",
    "PAUSED",
    "DONE",
)
EXECUTE_STAGE_ACTIVE_STATUSES = (
    "IN_PROGRESS",
    "BLOCKED",
    "AT_RISK",
    "PAUSED",
)
EXECUTE_OVERALL_PRIORITY = (
    "BLOCKED",
    "AT_RISK",
    "PAUSED",
    "NOT_STARTED",
    "IN_PROGRESS",
    "DONE",
)
ALLOWED_SEED_STYLES = {"concise", "balanced", "detailed"}


def _normalise_seed_style(value: str) -> str:
    v = str(value or "").strip().lower()
    if v in ALLOWED_SEED_STYLES:
        return v
    return "balanced"


def _normalise_seed_constraints(value: str) -> str:
    text = str(value or "").strip()
    if len(text) > 400:
        return text[:400].strip()
    return text


def _seed_style_blocks(seed_style: str, seed_constraints: str = "") -> list[str]:
    style = _normalise_seed_style(seed_style)
    blocks = ["Writing style controls:"]
    if style == "concise":
        blocks.extend(
            [
                "Use short sentences.",
                "One idea per sentence.",
                "Avoid qualifiers and filler.",
                "Avoid wording like high-level, robust, comprehensive, strategic.",
                "Prefer concrete verbs and clear actions.",
            ]
        )
    elif style == "detailed":
        blocks.extend(
            [
                "Use clear detail with practical depth.",
                "Prefer concrete specifics over abstract terms.",
                "Keep structure explicit and scannable.",
            ]
        )
    else:
        blocks.extend(
            [
                "Use balanced clarity.",
                "Avoid unnecessary qualifiers.",
                "Keep language practical and direct.",
            ]
        )
    if seed_constraints:
        blocks.append("User constraints: " + _normalise_seed_constraints(seed_constraints))
    return blocks


def _get_user_project_seed_defaults(project, user) -> tuple[str, str]:
    prefs = UserProjectPrefs.objects.filter(project=project, user=user).first()
    ui = prefs.ui_overrides if prefs and isinstance(prefs.ui_overrides, dict) else {}
    return (
        _normalise_seed_style(ui.get("rw_seed_style")),
        _normalise_seed_constraints(ui.get("rw_seed_constraints")),
    )


def _build_route_seed_from_intent(intent_payload: dict) -> dict:
    intent = _dict_or_empty(intent_payload)
    canonical_summary = str(intent.get("canonical_summary") or "").strip()
    statement = str(intent.get("statement") or "").strip()
    scope = str(intent.get("scope") or "").strip()
    assumptions = str(intent.get("assumptions") or "").strip()
    supporting_basis = str(intent.get("supporting_basis") or "").strip()
    uncertainties = str(intent.get("uncertainties_limits") or "").strip()

    assumptions_lines = []
    if assumptions:
        assumptions_lines.append(assumptions)
    if uncertainties:
        assumptions_lines.append("Uncertainties / limits:\n" + uncertainties)
    combined_assumptions = "\n\n".join(assumptions_lines).strip()

    raw = {
        "pdo_summary": canonical_summary or statement,
        "cko_alignment": {
            "stage1_inputs_match": supporting_basis or "Derived from INTENT anchor.",
            "final_outputs_match": statement or canonical_summary,
        },
        "planning_purpose": statement or canonical_summary,
        "planning_constraints": scope,
        "assumptions": combined_assumptions,
        "stages": [],
    }
    return normalise_pdo_payload(raw)


def _extract_json_dict_from_text(raw_text: str) -> dict | None:
    text = (raw_text or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    if "```" in text:
        parts = text.split("```")
        for chunk in parts[1::2]:
            candidate = chunk.strip()
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                continue
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return None


def _build_route_seed_from_intent_llm(
    *, intent_payload: dict, user, seed_style: str = "balanced", seed_constraints: str = ""
) -> dict | None:
    intent_text = build_cko_seed_text(intent_payload) or json.dumps(intent_payload, ensure_ascii=True, indent=2)
    base_route = _build_route_seed_from_intent(intent_payload)
    system_blocks = [
        "You are generating only ROUTE stages from an INTENT anchor.",
        "Return pane JSON as normal, but put strict JSON stage-map in output.",
        "In output, return ONLY this object schema:",
        "{",
        '  "stages": [',
        "    {",
        '      "title": "string",',
        '      "purpose": "string",',
        '      "inputs": "string",',
        '      "stage_process": "string",',
        '      "outputs": "string",',
        '      "assumptions": "string",',
        '      "duration_estimate": "string",',
        '      "risks_notes": "string"',
        "    }",
        "  ]",
        "}",
        "Rules: stages count 3 to 8. Concrete steps. Ordered flow. No markdown.",
    ]
    panes = generate_panes(
        "INTENT anchor source:\n" + intent_text + "\n\nProduce stage map now.",
        image_parts=None,
        system_blocks=system_blocks + _seed_style_blocks(seed_style, seed_constraints),
        user=user,
    )
    obj = _extract_json_dict_from_text(str(panes.get("output") or ""))
    if isinstance(obj, dict):
        stages = obj.get("stages")
        if isinstance(stages, list) and stages:
            merged = dict(base_route)
            merged["stages"] = stages
            norm = normalise_pdo_payload(merged)
            if isinstance(norm.get("stages"), list) and len(norm.get("stages") or []) > 0:
                return norm
    return None


def _normalise_intent_payload(payload: dict | None, *, default_provenance: str = "") -> dict:
    src = payload if isinstance(payload, dict) else {}
    out = {
        "canonical_summary": str(src.get("canonical_summary") or "").strip(),
        "scope": str(src.get("scope") or "").strip(),
        "statement": str(src.get("statement") or "").strip(),
        "supporting_basis": str(src.get("supporting_basis") or "").strip(),
        "assumptions": str(src.get("assumptions") or "").strip(),
        "alternatives_considered": str(src.get("alternatives_considered") or "").strip(),
        "uncertainties_limits": str(src.get("uncertainties_limits") or "").strip(),
        "provenance": str(src.get("provenance") or "").strip(),
    }
    if not out["alternatives_considered"]:
        out["alternatives_considered"] = "DEFERRED"
    if not out["uncertainties_limits"]:
        out["uncertainties_limits"] = "DEFERRED"
    if not out["provenance"]:
        out["provenance"] = default_provenance or "Seeded from accepted CKO."
    return out


def _build_intent_seed_from_cko_llm(
    *, source_payload: dict, user, seed_style: str = "balanced", seed_constraints: str = ""
) -> dict | None:
    source = _normalise_intent_payload(source_payload)
    system_blocks = [
        "You are producing an INTENT CKO payload.",
        "Return strict JSON only in output, no markdown.",
        "Fill all required fields with concise content.",
        "If unknown, write DEFERRED rather than leaving empty.",
        "Schema:",
        "{",
        '  "canonical_summary": "string <= 10 words",',
        '  "scope": "string",',
        '  "statement": "string",',
        '  "supporting_basis": "string",',
        '  "assumptions": "string",',
        '  "alternatives_considered": "string",',
        '  "uncertainties_limits": "string",',
        '  "provenance": "string"',
        "}",
    ]
    prompt = (
        "Accepted CKO source JSON:\n"
        + json.dumps(source, ensure_ascii=True, indent=2)
        + "\n\nReturn INTENT CKO JSON now."
    )
    panes = generate_panes(
        prompt,
        image_parts=None,
        system_blocks=system_blocks + _seed_style_blocks(seed_style, seed_constraints),
        user=user,
    )
    obj = _extract_json_dict_from_text(str(panes.get("output") or ""))
    if not isinstance(obj, dict):
        return None
    return _normalise_intent_payload(obj)


def _build_execute_stage_actions_llm(
    *, route_payload: dict, user, seed_style: str = "balanced", seed_constraints: str = ""
) -> dict[str, dict] | None:
    route_norm = normalise_pdo_payload(route_payload or {})
    stages = route_norm.get("stages") if isinstance(route_norm.get("stages"), list) else []
    if not stages:
        return None

    schema_lines = [
        "{",
        '  "stages": [',
        "    {",
        '      "stage_id": "S1",',
        '      "next_actions": "- action one\\n- action two",',
        '      "notes": "short execution notes"',
        "    }",
        "  ]",
        "}",
    ]
    system_blocks = [
        "You synthesise execution actions for each route stage.",
        "Keep stage ids unchanged. Do not invent stage ids.",
        "Return strict JSON only in output, no markdown.",
        "For each stage, write concrete next actions that move from inputs to outputs.",
        "Use short lines. Use '- ' bullets in next_actions.",
        "Keep notes short and practical.",
        "Output schema:",
        *schema_lines,
    ]
    prompt = (
        "ROUTE JSON:\n"
        + json.dumps(route_norm, ensure_ascii=True, indent=2)
        + "\n\nReturn stage execution actions now."
    )
    panes = generate_panes(
        prompt,
        image_parts=None,
        system_blocks=system_blocks + _seed_style_blocks(seed_style, seed_constraints),
        user=user,
    )
    obj = _extract_json_dict_from_text(str(panes.get("output") or ""))
    if not isinstance(obj, dict):
        return None
    rows = obj.get("stages")
    if not isinstance(rows, list):
        return None
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("stage_id") or "").strip()
        if not sid:
            continue
        out[sid] = {
            "next_actions": str(row.get("next_actions") or "").strip(),
            "notes": str(row.get("notes") or "").strip(),
        }
    return out or None


def _dict_or_empty(value):
    return value if isinstance(value, dict) else {}


def _summarise_changed_keys(before_obj: dict, after_obj: dict) -> list[str]:
    keys = sorted(set(before_obj.keys()) | set(after_obj.keys()))
    changed = []
    for key in keys:
        if before_obj.get(key) != after_obj.get(key):
            changed.append(key)
    return changed


def _compact_value(value):
    if isinstance(value, (dict, list)):
        try:
            text = json.dumps(value, ensure_ascii=True)
        except Exception:
            text = str(value)
    elif value is None:
        text = ""
    else:
        text = str(value)
    text = text.strip()
    if len(text) > 180:
        return text[:177] + "..."
    return text


def _build_audit_diff(before_obj: dict, after_obj: dict) -> list[dict]:
    changed = _summarise_changed_keys(before_obj, after_obj)
    rows = []
    for key in changed[:8]:
        rows.append(
            {
                "key": key,
                "before": _compact_value(before_obj.get(key)),
                "after": _compact_value(after_obj.get(key)),
            }
        )
    return rows


def _format_duration(delta) -> str:
    total_seconds = int(max(delta.total_seconds(), 0))
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def _build_stage_map(payload_obj: dict) -> dict:
    stage_map = {}
    stages = payload_obj.get("stages") if isinstance(payload_obj.get("stages"), list) else []
    for item in stages:
        if not isinstance(item, dict):
            continue
        stage_number = item.get("stage_number")
        stage_id = str(item.get("stage_id") or "").strip()
        key = stage_id or (f"S{stage_number}" if stage_number is not None else "")
        if not key:
            continue
        stage_map[key] = item
    return stage_map


def _compute_execute_overall_status(stages: list[dict], fallback: str = "") -> str:
    statuses = []
    for stage in stages:
        status = str(stage.get("status") or "").strip().upper()
        if status:
            statuses.append(status)
    for candidate in EXECUTE_OVERALL_PRIORITY:
        if candidate in statuses:
            return candidate
    return str(fallback or "").strip()


def _collate_stage_text(stages: list[dict], field_name: str) -> str:
    lines = []
    for stage in stages:
        value = str(stage.get(field_name) or "").strip()
        if not value:
            continue
        stage_number = stage.get("stage_number")
        stage_id = str(stage.get("stage_id") or "").strip()
        stage_label = f"Stage {stage_number}" if stage_number is not None else "Stage"
        if stage_id:
            stage_label += f" ({stage_id})"
        lines.append(f"{stage_label}: {value}")
    return "\n".join(lines)


def _record_anchor_audit(
    *,
    project,
    anchor,
    marker: str,
    changed_by,
    change_type: str,
    summary: str,
    status_before: str = "",
    status_after: str = "",
    before_content: str = "",
    after_content: str = "",
    before_json: dict | None = None,
    after_json: dict | None = None,
) -> None:
    ProjectAnchorAudit.objects.create(
        project=project,
        anchor=anchor,
        marker=marker,
        change_type=change_type,
        summary=summary[:255],
        status_before=(status_before or "").strip(),
        status_after=(status_after or "").strip(),
        before_content=(before_content or ""),
        after_content=(after_content or ""),
        before_content_json=before_json or {},
        after_content_json=after_json or {},
        changed_by=changed_by,
    )


@login_required
def project_review(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    request.session["rw_active_project_id"] = project.id
    request.session.modified = True
    allowed_view_styles = {"default", "cockpit", "mission"}
    requested_view_style = (request.GET.get("view") or "").strip().lower()
    if requested_view_style in allowed_view_styles:
        request.session["rw_view_style"] = requested_view_style
        request.session.modified = True
    session_view_style = (request.session.get("rw_view_style") or "").strip().lower()
    intent_view_style = session_view_style if session_view_style in allowed_view_styles else "default"
    if requested_view_style in allowed_view_styles:
        intent_view_style = requested_view_style
    pref_seed_style, pref_seed_constraints = _get_user_project_seed_defaults(project, request.user)
    seed_style = _normalise_seed_style(request.session.get("rw_seed_style") or pref_seed_style)
    seed_constraints = _normalise_seed_constraints(
        request.session.get("rw_seed_constraints") or pref_seed_constraints
    )
    if (request.GET.get("apply_seed_controls") or "").strip() == "1":
        seed_style = _normalise_seed_style(request.GET.get("seed_style") or seed_style)
        seed_constraints = _normalise_seed_constraints(request.GET.get("seed_constraints") or seed_constraints)
        request.session["rw_seed_style"] = seed_style
        request.session["rw_seed_constraints"] = seed_constraints
        request.session.modified = True
        if (request.GET.get("make_default") or "").strip() == "1":
            prefs, _ = UserProjectPrefs.objects.get_or_create(project=project, user=request.user)
            ui = prefs.ui_overrides if isinstance(prefs.ui_overrides, dict) else {}
            ui["rw_seed_style"] = seed_style
            ui["rw_seed_constraints"] = seed_constraints
            prefs.ui_overrides = ui
            prefs.save(update_fields=["ui_overrides", "updated_at"])
            messages.success(request, "Seed style applied and set as default for this project.")
        else:
            messages.success(request, "Seed style applied.")

    accepted_cko = None
    if project.defined_cko_id:
        accepted_cko = ProjectCKO.objects.filter(id=project.defined_cko_id, project=project).first()

    # Ensure INTENT anchor starts from accepted CKO so review has a concrete baseline.
    intent_anchor = ProjectAnchor.objects.filter(project=project, marker="INTENT").first()
    if accepted_cko and (
        intent_anchor is None
        or (
            not ((intent_anchor.content or "").strip())
            and not bool(intent_anchor.content_json)
        )
    ):
        seed_json = accepted_cko.content_json if isinstance(accepted_cko.content_json, dict) else {}
        seed_text = (accepted_cko.content_text or "").strip()
        if intent_anchor is None:
            intent_anchor = ProjectAnchor.objects.create(
                project=project,
                marker="INTENT",
                content=seed_text,
                content_json=seed_json,
                status=ProjectAnchor.Status.DRAFT,
                last_edited_by=request.user,
                last_edited_at=timezone.now(),
            )
        else:
            intent_anchor.content = seed_text
            intent_anchor.content_json = seed_json
            intent_anchor.status = ProjectAnchor.Status.DRAFT
            intent_anchor.proposed_by = None
            intent_anchor.proposed_at = None
            intent_anchor.locked_by = None
            intent_anchor.locked_at = None
            intent_anchor.last_edited_by = request.user
            intent_anchor.last_edited_at = timezone.now()
            intent_anchor.save(
                update_fields=[
                    "content",
                    "content_json",
                    "status",
                    "proposed_by",
                    "proposed_at",
                    "locked_by",
                    "locked_at",
                    "last_edited_by",
                    "last_edited_at",
                    "updated_at",
                ]
            )

    anchors = {a.marker: a for a in ProjectAnchor.objects.filter(project=project)}
    audit_rows = (
        ProjectAnchorAudit.objects
        .filter(project=project)
        .select_related("changed_by")
        .order_by("-created_at")[:200]
    )
    audit_map = {}
    execute_status_events = []
    execute_stage_latest_changes = {}
    for row in audit_rows:
        before_obj = _dict_or_empty(row.before_content_json)
        after_obj = _dict_or_empty(row.after_content_json)
        before_overall = str(before_obj.get("overall_status") or "").strip()
        after_overall = str(after_obj.get("overall_status") or "").strip()
        if row.marker == "EXECUTE" and before_overall != after_overall and after_overall:
            execute_status_events.append(
                {
                    "id": row.id,
                    "created_at": row.created_at,
                    "overall_status_after": after_overall,
                }
            )
        if row.marker == "EXECUTE":
            before_stage_map = _build_stage_map(before_obj)
            after_stage_map = _build_stage_map(after_obj)
            stage_keys = set(before_stage_map.keys()) | set(after_stage_map.keys())
            for stage_key in stage_keys:
                if stage_key in execute_stage_latest_changes:
                    continue
                before_stage = before_stage_map.get(stage_key) or {}
                after_stage = after_stage_map.get(stage_key) or {}
                status_before = str(before_stage.get("status") or "").strip()
                status_after = str(after_stage.get("status") or "").strip()
                next_before = str(before_stage.get("next_actions") or "").strip()
                next_after = str(after_stage.get("next_actions") or "").strip()
                note_before = str(before_stage.get("notes") or "").strip()
                note_after = str(after_stage.get("notes") or "").strip()
                if (
                    status_before != status_after
                    or next_before != next_after
                    or note_before != note_after
                ):
                    execute_stage_latest_changes[stage_key] = {
                        "created_at": row.created_at,
                        "status_before": status_before,
                        "status_after": status_after,
                        "next_actions_after": next_after,
                        "notes_after": note_after,
                    }
        item = {
            "id": row.id,
            "created_at": row.created_at,
            "changed_by": row.changed_by,
            "change_type": row.change_type,
            "summary": row.summary,
            "status_before": row.status_before,
            "status_after": row.status_after,
            "diff_rows": _build_audit_diff(before_obj, after_obj),
            "overall_status_after": "",
            "overall_status_duration_text": "",
        }
        bucket = audit_map.setdefault(row.marker, [])
        if len(bucket) < 20:
            bucket.append(item)
    route_versions = []
    for row in audit_rows:
        if row.marker != "ROUTE":
            continue
        if row.change_type != ProjectAnchorAudit.ChangeType.RESEED:
            continue
        before_json = _dict_or_empty(row.before_content_json)
        after_json = _dict_or_empty(row.after_content_json)
        if not before_json and not after_json:
            continue
        route_versions.append(
            {
                "audit_id": row.id,
                "created_at": row.created_at,
                "changed_by_name": getattr(row.changed_by, "username", "") or "",
                "summary": row.summary or "",
            }
        )
        if len(route_versions) >= 12:
            break

    status_meta = {}
    now = timezone.now()
    for idx, ev in enumerate(execute_status_events):
        if idx == 0:
            end_time = now
        else:
            end_time = execute_status_events[idx - 1]["created_at"]
        delta = end_time - ev["created_at"]
        status_meta[ev["id"]] = {
            "overall_status_after": ev["overall_status_after"],
            "overall_status_duration_text": _format_duration(delta),
        }

    execute_items = audit_map.get("EXECUTE", [])
    for item in execute_items:
        meta = status_meta.get(item.get("id"))
        if not meta:
            continue
        item["overall_status_after"] = meta["overall_status_after"]
        item["overall_status_duration_text"] = meta["overall_status_duration_text"]
    chats = {
        rc.marker: rc.chat_id
        for rc in ProjectReviewChat.objects.filter(project=project, user=request.user)
    }
    stage_chats = {
        (rc.marker, rc.stage_number): rc.chat_id
        for rc in ProjectReviewStageChat.objects.filter(project=project, user=request.user)
    }
    intent_anchor = anchors.get("INTENT")
    intent_locked = bool(
        (intent_anchor and intent_anchor.status == ProjectAnchor.Status.PASS_LOCKED)
        or (accepted_cko is not None)
    )

    chat_ids = set(chats.values()) | set(stage_chats.values())
    review_chat_id_raw = (request.GET.get("review_chat_id") or "").strip()
    open_param = (request.GET.get("review_chat_open") or "").strip()
    selected_chat_id = int(review_chat_id_raw) if review_chat_id_raw.isdigit() else None
    if selected_chat_id and selected_chat_id in chat_ids:
        request.session["rw_active_chat_id"] = selected_chat_id
        request.session.modified = True
    chat_ctx_map = {}
    if chat_ids:
        chat_objs = {c.id: c for c in ChatWorkspace.objects.filter(id__in=chat_ids)}
        show_system = request.GET.get("system") in ("1", "true", "yes")
        for chat_id, chat in chat_objs.items():
            ctx = build_chat_turn_context(request, chat)
            qs = request.GET.copy()
            qs["review_chat_id"] = str(chat.id)
            qs["review_chat_open"] = "1"
            if not show_system:
                qs.pop("system", None)
            qs.pop("turn", None)
            ctx["chat"] = chat
            ctx["qs_base"] = qs.urlencode()
            ctx["show_system"] = bool(show_system)
            if not show_system:
                items = [t for t in ctx.get("turn_items", []) if t.get("kind") != "system"]
                ctx["turn_items"] = items
                ctx["turn_items_rev"] = list(reversed(items))
                if ctx.get("active_turn") and ctx["active_turn"].get("kind") == "system":
                    last_turn = None
                    for it in reversed(items):
                        if it.get("kind") == "turn":
                            last_turn = it
                            break
                    if last_turn:
                        ctx["active_turn"] = last_turn
                        ctx["is_system_turn"] = False
            if selected_chat_id == chat.id and open_param in ("0", "1"):
                ctx["is_open"] = (open_param == "1")
            else:
                ctx["is_open"] = False
            chat_ctx_map[chat.id] = ctx

    sections = []
    for marker, label, anchor_type in MARKERS:
        anchor = anchors.get(marker)
        content_json = (anchor.content_json if anchor else {}) or {}
        content_text = (anchor.content or "") if anchor else ""
        content_json_text = ""
        content_html = ""
        intent_fields = {}
        route_fields = {}
        route_stages = []
        execute_fields = {}
        execute_status_is_standard = False
        execute_outputs = []
        execute_stages = []
        execute_stage_summary = []
        has_content = False
        if content_json:
            content_json_text = json.dumps(content_json, indent=2, ensure_ascii=True)
            content_html = render_artefact_html(ANCHOR_KIND.get(marker, ""), content_json)
            has_content = True
        elif content_text:
            payload = None
            try:
                payload = json.loads(content_text)
            except Exception:
                payload = None
            if isinstance(payload, dict):
                content_json_text = json.dumps(payload, indent=2, ensure_ascii=True)
                content_html = render_artefact_html(ANCHOR_KIND.get(marker, ""), payload)
                if marker == "INTENT":
                    content_json = payload
                has_content = True
            elif content_text:
                has_content = True

        if marker == "INTENT":
            intent_fields = {
                "canonical_summary": (content_json.get("canonical_summary") or ""),
                "scope": (content_json.get("scope") or ""),
                "statement": (content_json.get("statement") or ""),
                "supporting_basis": (content_json.get("supporting_basis") or ""),
                "assumptions": (content_json.get("assumptions") or ""),
                "alternatives_considered": (content_json.get("alternatives_considered") or ""),
                "uncertainties_limits": (content_json.get("uncertainties_limits") or ""),
                "provenance": (content_json.get("provenance") or ""),
            }
        if marker == "ROUTE":
            normalised = normalise_pdo_payload(content_json)
            route_fields = {
                "pdo_summary": (normalised.get("pdo_summary") or ""),
                "planning_purpose": (normalised.get("planning_purpose") or ""),
                "planning_constraints": (normalised.get("planning_constraints") or ""),
                "assumptions": (normalised.get("assumptions") or ""),
                "cko_alignment_stage1_inputs_match": (
                    (normalised.get("cko_alignment") or {}).get("stage1_inputs_match") or ""
                ),
                "cko_alignment_final_outputs_match": (
                    (normalised.get("cko_alignment") or {}).get("final_outputs_match") or ""
                ),
            }
            stages = normalised.get("stages") or []
            if isinstance(stages, list):
                for item in stages:
                    if not isinstance(item, dict):
                        continue
                    stage_number = item.get("stage_number")
                    stage_id = item.get("stage_id") or (f"S{stage_number}" if stage_number else "")
                    route_stages.append(
                        {
                            "stage_id": stage_id,
                            "stage_number": stage_number,
                            "status": item.get("status") or "",
                            "title": item.get("title") or "",
                            "purpose": item.get("purpose") or "",
                            "inputs": item.get("inputs") or "",
                            "stage_process": item.get("stage_process") or "",
                            "outputs": item.get("outputs") or "",
                            "assumptions": item.get("assumptions") or "",
                            "duration_estimate": item.get("duration_estimate") or "",
                            "risks_notes": item.get("risks_notes") or "",
                            "stage_chat_id": stage_chats.get(("ROUTE", stage_number)),
                            "stage_chat_ctx": chat_ctx_map.get(stage_chats.get(("ROUTE", stage_number))),
                        }
                    )
            if not route_stages:
                route_stages.append(
                    {
                        "stage_number": 1,
                        "status": "",
                        "title": "",
                        "purpose": "",
                        "inputs": "",
                        "stage_process": "",
                        "outputs": "",
                        "assumptions": "",
                        "duration_estimate": "",
                        "risks_notes": "",
                    }
                )
            summary_chat_id = stage_chats.get(("ROUTE", 0))
        if marker == "EXECUTE":
            exec_payload = content_json if isinstance(content_json, dict) else {}
            source_route = exec_payload.get("source_route") if isinstance(exec_payload.get("source_route"), dict) else {}
            execute_fields = {
                "artefact_type": exec_payload.get("artefact_type") or "",
                "marker": exec_payload.get("marker") or "",
                "version": exec_payload.get("version") or "",
                "route_version": source_route.get("route_version") or "",
                "route_hash": source_route.get("route_hash") or "",
                "overall_status": exec_payload.get("overall_status") or "",
                "current_stage_id": exec_payload.get("current_stage_id") or "",
                "next_actions_summary": "",
                "notes_summary": "",
            }
            execute_status_is_standard = execute_fields["overall_status"] in (
                "IN_PROGRESS",
                "BLOCKED",
                "AT_RISK",
                "NOT_STARTED",
                "PAUSED",
                "DONE",
            )
            outputs = exec_payload.get("outputs") if isinstance(exec_payload.get("outputs"), list) else []
            for item in outputs:
                if not isinstance(item, dict):
                    continue
                execute_outputs.append(
                    {
                        "output_id": item.get("output_id") or "",
                        "title": item.get("title") or "",
                        "status": item.get("status") or "",
                        "stage_id": item.get("stage_id") or "",
                    }
                )
            stages = exec_payload.get("stages") if isinstance(exec_payload.get("stages"), list) else []
            for item in stages:
                if not isinstance(item, dict):
                    continue
                stage_number = item.get("stage_number")
                stage_id = item.get("stage_id") or (f"S{stage_number}" if stage_number else "")
                execute_stages.append(
                    {
                        "stage_id": stage_id,
                        "stage_number": stage_number,
                        "status": item.get("status") or "",
                        "title": item.get("title") or "",
                        "purpose": item.get("purpose") or "",
                        "inputs": item.get("inputs") or "",
                        "stage_process": item.get("stage_process") or "",
                        "outputs": item.get("outputs") or "",
                        "assumptions": item.get("assumptions") or "",
                        "duration_estimate": item.get("duration_estimate") or "",
                        "risks_notes": item.get("risks_notes") or "",
                        "next_actions": item.get("next_actions") or "",
                        "notes": item.get("notes") or "",
                        "outputs_due": item.get("outputs_due") or [],
                        "outputs_status": item.get("outputs_status") or [],
                        "work_items": item.get("work_items") or [],
                        "decisions": item.get("decisions") or [],
                        "blockers": item.get("blockers") or [],
                        "evidence": item.get("evidence") or [],
                        "stage_chat_id": stage_chats.get(("EXECUTE", stage_number)),
                        "stage_chat_ctx": chat_ctx_map.get(stage_chats.get(("EXECUTE", stage_number))),
                    }
                )
            computed_status = _compute_execute_overall_status(execute_stages, fallback=execute_fields["overall_status"])
            execute_fields["overall_status"] = computed_status
            execute_status_is_standard = computed_status in (
                "IN_PROGRESS",
                "BLOCKED",
                "AT_RISK",
                "NOT_STARTED",
                "PAUSED",
                "DONE",
            )
            execute_fields["next_actions_summary"] = _collate_stage_text(execute_stages, "next_actions")
            execute_fields["notes_summary"] = _collate_stage_text(execute_stages, "notes")
            for stage in execute_stages:
                stage_status = str(stage.get("status") or "").strip()
                stage_id = str(stage.get("stage_id") or "").strip()
                stage_number = stage.get("stage_number")
                stage_key = stage_id or (f"S{stage_number}" if stage_number is not None else "")
                latest_change = execute_stage_latest_changes.get(stage_key)
                is_active = stage_status in EXECUTE_STAGE_ACTIVE_STATUSES
                if not is_active and not latest_change:
                    continue
                latest_action = str(stage.get("next_actions") or "").strip()
                latest_note = str(stage.get("notes") or "").strip()
                if not latest_action and latest_change:
                    latest_action = latest_change.get("next_actions_after") or ""
                if not latest_note and latest_change:
                    latest_note = latest_change.get("notes_after") or ""
                execute_stage_summary.append(
                    {
                        "stage_number": stage_number,
                        "stage_id": stage_id,
                        "title": stage.get("title") or "",
                        "status": stage_status,
                        "is_active": is_active,
                        "has_change": bool(latest_change),
                        "latest_action": latest_action,
                        "latest_note": latest_note,
                        "changed_at": (latest_change.get("created_at") if latest_change else None),
                    }
                )
            execute_stage_summary.sort(key=lambda x: (x.get("stage_number") is None, x.get("stage_number") or 0))
        sections.append(
            {
                "marker": marker,
                "label": label,
                "anchor_type": anchor_type,
                "status": anchor.status if anchor else "DRAFT",
                "last_edited_at": (anchor.last_edited_at if anchor else None),
                "last_edited_by": ((getattr(anchor.last_edited_by, "username", "") or "") if anchor else ""),
                "status_badge_class": (
                    "bg-success"
                    if (anchor and anchor.status == "PASS_LOCKED")
                    else ("bg-warning text-dark" if (anchor and anchor.status == "PROPOSED") else "bg-secondary")
                ),
                "content": (anchor.content or "") if anchor else "",
                "content_json_text": content_json_text,
                "content_html": content_html,
                "intent_fields": intent_fields,
                "route_fields": route_fields,
                "route_stages": route_stages,
                "route_summary_chat_id": summary_chat_id if marker == "ROUTE" else None,
                "execute_fields": execute_fields,
                "execute_status_is_standard": execute_status_is_standard,
                "execute_stage_status_choices": EXECUTE_STAGE_STATUS_CHOICES,
                "execute_outputs": execute_outputs,
                "execute_stages": execute_stages,
                "execute_stage_summary": execute_stage_summary,
                "chat_id": chats.get(marker),
                "chat_ctx": chat_ctx_map.get(chats.get(marker)),
                "can_seed_from_intent": bool(marker == "ROUTE" and intent_locked),
                "can_reseed_execute": bool(marker == "EXECUTE" and anchors.get("ROUTE")),
                "has_content": has_content,
                "audit_entries": audit_map.get(marker, []),
                "route_versions": route_versions if marker == "ROUTE" else [],
            }
        )

    review_edit = (request.GET.get("review_edit") or "").strip().lower()
    review_anchor_open = (request.GET.get("review_anchor_open") or "").strip().lower() in ("1", "true", "yes")
    base_qs = request.GET.copy()
    intent_view_urls = {}
    for style_name in ("default", "cockpit", "mission"):
        qs = base_qs.copy()
        qs["view"] = style_name
        intent_view_urls[style_name] = "?" + qs.urlencode()
    return render(
        request,
        "projects/project_review.html",
        {
            "project": project,
            "sections": sections,
            "review_edit": review_edit,
            "review_anchor_open": review_anchor_open,
            "intent_view_style": intent_view_style,
            "intent_view_urls": intent_view_urls,
            "seed_style": seed_style,
            "seed_constraints": seed_constraints,
        },
    )


@login_required
def project_review_print_intent(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    anchor = ProjectAnchor.objects.filter(project=project, marker="INTENT").first()
    view_style = (request.GET.get("view") or "").strip().lower()
    if view_style not in {"default", "cockpit", "mission"}:
        view_style = "default"

    content_text = (anchor.content or "") if anchor else ""
    content_json = (anchor.content_json if anchor else {}) or {}
    if not content_json and content_text:
        try:
            payload = json.loads(content_text)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            content_json = payload

    content_html = ""
    if content_json:
        content_html = render_artefact_html("CKO", content_json)
    intent_fields = _normalise_intent_payload(content_json if isinstance(content_json, dict) else {})

    return render(
        request,
        "projects/review_print_intent.html",
        {
            "project": project,
            "anchor": anchor,
            "view_style": view_style,
            "intent_fields": intent_fields,
            "content_text": content_text,
            "content_html": content_html,
        },
    )


@login_required
def project_review_print_route(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    anchor = ProjectAnchor.objects.filter(project=project, marker="ROUTE").first()
    view_style = (request.GET.get("view") or "").strip().lower()
    if view_style not in {"default", "cockpit", "mission"}:
        view_style = "default"

    content_text = (anchor.content or "") if anchor else ""
    content_json = (anchor.content_json if anchor else {}) or {}
    if not content_json and content_text:
        try:
            payload = json.loads(content_text)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            content_json = payload

    normalised = normalise_pdo_payload(content_json)
    route_fields = {}
    route_stages = []
    if normalised:
        route_fields = {
            "pdo_summary": str(normalised.get("pdo_summary") or "").strip(),
            "planning_purpose": str(normalised.get("planning_purpose") or "").strip(),
            "planning_constraints": str(normalised.get("planning_constraints") or "").strip(),
            "assumptions": str(normalised.get("assumptions") or "").strip(),
            "cko_alignment_stage1_inputs_match": str(
                (normalised.get("cko_alignment") or {}).get("stage1_inputs_match") or ""
            ).strip(),
            "cko_alignment_final_outputs_match": str(
                (normalised.get("cko_alignment") or {}).get("final_outputs_match") or ""
            ).strip(),
        }
        for item in normalised.get("stages") or []:
            if not isinstance(item, dict):
                continue
            stage_number = item.get("stage_number")
            route_stages.append(
                {
                    "stage_number": stage_number if isinstance(stage_number, int) else None,
                    "stage_id": str(item.get("stage_id") or "").strip(),
                    "status": str(item.get("status") or "").strip(),
                    "title": str(item.get("title") or "").strip(),
                    "purpose": str(item.get("purpose") or "").strip(),
                    "inputs": str(item.get("inputs") or "").strip(),
                    "stage_process": str(item.get("stage_process") or "").strip(),
                    "outputs": str(item.get("outputs") or "").strip(),
                    "assumptions": str(item.get("assumptions") or "").strip(),
                    "duration_estimate": str(item.get("duration_estimate") or "").strip(),
                    "risks_notes": str(item.get("risks_notes") or "").strip(),
                }
            )
    content_html = render_artefact_html("PDO", normalised) if normalised else ""

    return render(
        request,
        "projects/review_print_route.html",
        {
            "project": project,
            "anchor": anchor,
            "view_style": view_style,
            "route_fields": route_fields,
            "route_stages": route_stages,
            "content_text": content_text,
            "content_html": content_html,
        },
    )


@login_required
def project_review_print_execute(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    anchor = ProjectAnchor.objects.filter(project=project, marker="EXECUTE").first()
    view_style = (request.GET.get("view") or "").strip().lower()
    if view_style not in {"default", "cockpit", "mission"}:
        view_style = "default"

    content_text = (anchor.content or "") if anchor else ""
    content_json = (anchor.content_json if anchor else {}) or {}
    if not content_json and content_text:
        try:
            parsed = json.loads(content_text)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            content_json = parsed

    exec_payload = _dict_or_empty(content_json)
    execute_stages = []
    for item in exec_payload.get("stages") or []:
        if not isinstance(item, dict):
            continue
        stage_number = item.get("stage_number")
        execute_stages.append(
            {
                "stage_number": stage_number if isinstance(stage_number, int) else None,
                "stage_id": str(item.get("stage_id") or "").strip(),
                "status": str(item.get("status") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "next_actions": str(item.get("next_actions") or "").strip(),
                "notes": str(item.get("notes") or "").strip(),
                "purpose": str(item.get("purpose") or "").strip(),
                "inputs": str(item.get("inputs") or "").strip(),
                "stage_process": str(item.get("stage_process") or "").strip(),
                "outputs": str(item.get("outputs") or "").strip(),
                "assumptions": str(item.get("assumptions") or "").strip(),
                "duration_estimate": str(item.get("duration_estimate") or "").strip(),
                "risks_notes": str(item.get("risks_notes") or "").strip(),
            }
        )
    execute_fields = {
        "overall_status": _compute_execute_overall_status(
            execute_stages, fallback=str(exec_payload.get("overall_status") or "").strip()
        ),
        "current_stage_id": str(exec_payload.get("current_stage_id") or "").strip(),
        "version": str(exec_payload.get("version") or "").strip(),
        "next_actions_summary": _collate_stage_text(execute_stages, "next_actions"),
        "notes_summary": _collate_stage_text(execute_stages, "notes"),
    }
    content_html = render_artefact_html("EXECUTE", exec_payload) if exec_payload else ""

    return render(
        request,
        "projects/review_print_execute.html",
        {
            "project": project,
            "anchor": anchor,
            "view_style": view_style,
            "execute_fields": execute_fields,
            "execute_stages": execute_stages,
            "content_text": content_text,
            "content_html": content_html,
        },
    )


@require_POST
@login_required
def project_review_anchor_update(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    wants_json = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or (request.POST.get("ajax") or "").strip() == "1"
    )
    marker = (request.POST.get("marker") or "").strip().upper()
    markers = {m[0] for m in MARKERS}
    if marker not in markers:
        messages.error(request, "Unknown review marker.")
        return redirect("projects:project_review", project_id=project.id)

    raw_text = (request.POST.get("content") or "").strip()
    anchor, _ = ProjectAnchor.objects.get_or_create(
        project=project,
        marker=marker,
        defaults={"content": "", "status": ProjectAnchor.Status.DRAFT},
    )
    before_status = anchor.status
    before_content = (anchor.content or "")
    before_json = _dict_or_empty(anchor.content_json).copy()
    if anchor.status == ProjectAnchor.Status.PASS_LOCKED:
        anchor.status = ProjectAnchor.Status.DRAFT
        anchor.proposed_by = None
        anchor.proposed_at = None
        anchor.locked_by = None
        anchor.locked_at = None

    payload = None
    if marker == "INTENT":
        fields = {
            "canonical_summary": (request.POST.get("cko_canonical_summary") or "").strip(),
            "scope": (request.POST.get("cko_scope") or "").strip(),
            "statement": (request.POST.get("cko_statement") or "").strip(),
            "supporting_basis": (request.POST.get("cko_supporting_basis") or "").strip(),
            "assumptions": (request.POST.get("cko_assumptions") or "").strip(),
            "alternatives_considered": (request.POST.get("cko_alternatives_considered") or "").strip(),
            "uncertainties_limits": (request.POST.get("cko_uncertainties_limits") or "").strip(),
            "provenance": (request.POST.get("cko_provenance") or "").strip(),
        }
        if any(v for v in fields.values()):
            normalised = {}
            for k, v in fields.items():
                if v:
                    normalised[k] = normalise_sections(v)
                else:
                    normalised[k] = v
            payload = normalised
    if marker == "ROUTE":
        route_action = (request.POST.get("action") or "").strip().lower()
        route_fields = {
            "pdo_summary": (request.POST.get("route_pdo_summary") or "").strip(),
            "planning_purpose": (request.POST.get("route_planning_purpose") or "").strip(),
            "planning_constraints": (request.POST.get("route_planning_constraints") or "").strip(),
            "assumptions": (request.POST.get("route_assumptions") or "").strip(),
            "cko_alignment_stage1_inputs_match": (request.POST.get("route_cko_stage1") or "").strip(),
            "cko_alignment_final_outputs_match": (request.POST.get("route_cko_final") or "").strip(),
        }
        stages = []
        stage_numbers = request.POST.getlist("route_stage_number")
        for idx, num_raw in enumerate(stage_numbers, start=1):
            try:
                stage_number = int(num_raw)
            except Exception:
                stage_number = idx
            prefix = f"route_stage_{idx}_"
            stage_id = (request.POST.get(prefix + "stage_id") or "").strip() or f"S{stage_number}"
            stages.append(
                {
                    "stage_id": stage_id,
                    "stage_number": stage_number,
                    "status": (request.POST.get(prefix + "status") or "").strip(),
                    "title": (request.POST.get(prefix + "title") or "").strip(),
                    "purpose": (request.POST.get(prefix + "purpose") or "").strip(),
                    "inputs": (request.POST.get(prefix + "inputs") or "").strip(),
                    "stage_process": (request.POST.get(prefix + "stage_process") or "").strip(),
                    "outputs": (request.POST.get(prefix + "outputs") or "").strip(),
                    "assumptions": (request.POST.get(prefix + "assumptions") or "").strip(),
                    "duration_estimate": (request.POST.get(prefix + "duration_estimate") or "").strip(),
                    "risks_notes": (request.POST.get(prefix + "risks_notes") or "").strip(),
                }
            )
        if route_action.startswith("save_route_stage:"):
            try:
                target_idx = int(route_action.split(":", 1)[1])
            except Exception:
                target_idx = 0
            if target_idx <= 0 or target_idx > len(stages):
                if wants_json:
                    return JsonResponse({"ok": False, "message": "Invalid stage save target."}, status=400)
                messages.error(request, "Invalid stage save target.")
                return redirect(reverse("projects:project_review", args=[project.id]) + "?review_edit=route&review_anchor_open=1#review-route")

            current = normalise_pdo_payload(anchor.content_json if isinstance(anchor.content_json, dict) else {})
            current_stages = list(current.get("stages") or [])
            staged = stages[target_idx - 1]
            stage_id = str(staged.get("stage_id") or "").strip()
            stage_num = int(staged.get("stage_number") or 0)
            updated = False
            for i, item in enumerate(current_stages):
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("stage_id") or "").strip()
                try:
                    item_num = int(item.get("stage_number") or 0)
                except Exception:
                    item_num = 0
                if (stage_id and item_id == stage_id) or (stage_num and item_num == stage_num):
                    current_stages[i] = staged
                    updated = True
                    break
            if not updated:
                current_stages.append(staged)
            current["stages"] = current_stages
            payload = normalise_pdo_payload(current)
        elif any(v for v in route_fields.values()) or stages:
            payload = {
                "pdo_summary": route_fields["pdo_summary"],
                "cko_alignment": {
                    "stage1_inputs_match": route_fields["cko_alignment_stage1_inputs_match"],
                    "final_outputs_match": route_fields["cko_alignment_final_outputs_match"],
                },
                "planning_purpose": route_fields["planning_purpose"],
                "planning_constraints": route_fields["planning_constraints"],
                "assumptions": route_fields["assumptions"],
                "stages": stages,
            }
    if marker == "EXECUTE":
        exec_base = _dict_or_empty(anchor.content_json).copy()
        source_route = _dict_or_empty(exec_base.get("source_route")).copy()
        route_version = (request.POST.get("execute_route_version") or "").strip() or str(source_route.get("route_version") or "")
        route_hash = (request.POST.get("execute_route_hash") or "").strip() or str(source_route.get("route_hash") or "")
        source_route["route_version"] = route_version
        source_route["route_hash"] = route_hash

        execute_fields = {
            "current_stage_id": (request.POST.get("execute_current_stage_id") or "").strip(),
        }

        existing_stages = exec_base.get("stages") if isinstance(exec_base.get("stages"), list) else []
        existing_by_stage_id = {}
        existing_by_stage_number = {}
        for item in existing_stages:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("stage_id") or "").strip()
            snum = item.get("stage_number")
            if sid:
                existing_by_stage_id[sid] = item
            if snum is not None:
                existing_by_stage_number[str(snum)] = item

        stages = []
        stage_numbers = request.POST.getlist("execute_stage_number")
        for idx, num_raw in enumerate(stage_numbers, start=1):
            num_text = str(num_raw or "").strip()
            if not num_text:
                continue
            try:
                stage_number = int(num_text)
            except Exception:
                stage_number = idx
            prefix = f"execute_stage_{idx}_"
            stage_id = (request.POST.get(prefix + "stage_id") or "").strip() or f"S{stage_number}"
            existing = existing_by_stage_id.get(stage_id) or existing_by_stage_number.get(str(stage_number)) or {}
            stage_payload = {
                "stage_id": stage_id,
                "stage_number": stage_number,
                "status": (request.POST.get(prefix + "status") or "").strip(),
                "title": str(existing.get("title") or "").strip(),
                "purpose": (request.POST.get(prefix + "purpose") or "").strip(),
                "inputs": (request.POST.get(prefix + "inputs") or "").strip(),
                "stage_process": (request.POST.get(prefix + "stage_process") or "").strip(),
                "outputs": (request.POST.get(prefix + "outputs") or "").strip(),
                "assumptions": (request.POST.get(prefix + "assumptions") or "").strip(),
                "duration_estimate": (request.POST.get(prefix + "duration_estimate") or "").strip(),
                "risks_notes": (request.POST.get(prefix + "risks_notes") or "").strip(),
                "next_actions": (request.POST.get(prefix + "next_actions") or "").strip(),
                "notes": (request.POST.get(prefix + "notes") or "").strip(),
                "outputs_due": existing.get("outputs_due") if isinstance(existing.get("outputs_due"), list) else [],
                "outputs_status": existing.get("outputs_status") if isinstance(existing.get("outputs_status"), list) else [],
                "work_items": existing.get("work_items") if isinstance(existing.get("work_items"), list) else [],
                "decisions": existing.get("decisions") if isinstance(existing.get("decisions"), list) else [],
                "blockers": existing.get("blockers") if isinstance(existing.get("blockers"), list) else [],
                "evidence": existing.get("evidence") if isinstance(existing.get("evidence"), list) else [],
            }
            stages.append(stage_payload)
        overall_status = _compute_execute_overall_status(stages, fallback=str(exec_base.get("overall_status") or "").strip())
        next_actions_summary = _collate_stage_text(stages, "next_actions")
        notes_summary = _collate_stage_text(stages, "notes")

        if (
            any(v for v in execute_fields.values())
            or stages
            or source_route
        ):
            payload = exec_base
            payload["artefact_type"] = str(exec_base.get("artefact_type") or "EXECUTE")
            payload["marker"] = "EXECUTE"
            payload["version"] = str(exec_base.get("version") or "")
            payload["source_route"] = source_route
            payload["overall_status"] = overall_status
            payload["current_stage_id"] = execute_fields["current_stage_id"]
            payload["today_focus"] = next_actions_summary
            payload["notes"] = notes_summary
            payload["outputs"] = exec_base.get("outputs") if isinstance(exec_base.get("outputs"), list) else []
            payload["stages"] = stages

    if payload is None and raw_text:
        def _clean_json_text(raw: str) -> str:
            cleaned = raw
            cleaned = re.sub(r'">(\s*[}\]])', r'"\1', cleaned)
            cleaned = re.sub(r'>\s*(?=[}\]])', "", cleaned)
            cleaned = re.sub(r'>\s*$', "", cleaned)
            return cleaned
        try:
            payload = json.loads(_clean_json_text(raw_text))
        except Exception:
            payload = None
        if payload is None and marker in ("ROUTE", "EXECUTE") and raw_text.lstrip().startswith("{"):
            messages.error(request, marker + " update requires valid JSON. Check for stray characters like '>' or missing quotes.")
            return redirect("projects:project_review", project_id=project.id)

    if isinstance(payload, dict):
        anchor.content_json = payload
        anchor.content = ""
        anchor.last_edited_by = request.user
        anchor.last_edited_at = timezone.now()
        anchor.save(update_fields=[
            "content_json",
            "content",
            "status",
            "proposed_by",
            "proposed_at",
            "locked_by",
            "locked_at",
            "last_edited_by",
            "last_edited_at",
            "updated_at",
        ])
        after_json = _dict_or_empty(anchor.content_json).copy()
        changed_keys = _summarise_changed_keys(before_json, after_json)
        if changed_keys:
            summary = marker + " JSON saved. Fields: " + ", ".join(changed_keys[:8])
            if len(changed_keys) > 8:
                summary += ", ..."
        else:
            summary = marker + " JSON saved."
        _record_anchor_audit(
            project=project,
            anchor=anchor,
            marker=marker,
            changed_by=request.user,
            change_type="UPDATE",
            summary=summary,
            status_before=before_status,
            status_after=anchor.status,
            before_content=before_content,
            after_content=anchor.content or "",
            before_json=before_json,
            after_json=after_json,
        )
        if marker == "ROUTE" and (request.POST.get("action") or "").strip().lower().startswith("save_route_stage:"):
            if wants_json:
                return JsonResponse({"ok": True, "message": "Stage saved.", "marker": marker})
            messages.success(request, "Stage saved.")
        else:
            if wants_json:
                return JsonResponse({"ok": True, "message": "Anchor JSON saved.", "marker": marker})
            messages.success(request, "Anchor JSON saved.")
    else:
        anchor.content = normalise_sections(raw_text)
        anchor.content_json = {}
        anchor.last_edited_by = request.user
        anchor.last_edited_at = timezone.now()
        anchor.save(update_fields=[
            "content",
            "content_json",
            "status",
            "proposed_by",
            "proposed_at",
            "locked_by",
            "locked_at",
            "last_edited_by",
            "last_edited_at",
            "updated_at",
        ])
        _record_anchor_audit(
            project=project,
            anchor=anchor,
            marker=marker,
            changed_by=request.user,
            change_type="UPDATE",
            summary=marker + " text saved.",
            status_before=before_status,
            status_after=anchor.status,
            before_content=before_content,
            after_content=anchor.content or "",
            before_json=before_json,
            after_json={},
        )
        if wants_json:
            return JsonResponse({"ok": True, "message": "Anchor text saved.", "marker": marker})
        messages.success(request, "Anchor text saved.")

    return redirect(
        reverse("projects:project_review", args=[project.id])
        + "#review-"
        + marker.lower()
    )


@require_POST
@login_required
def project_review_chat_open(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    marker = (request.POST.get("marker") or "").strip().upper()
    markers = {m[0] for m in MARKERS}
    if marker not in markers:
        messages.error(request, "Unknown review marker.")
        return redirect("projects:project_review", project_id=project.id)

    seed = (
        "You are helping review and refine the "
        + marker
        + " for this project.\n"
        + "Your role is to:\n"
        + "- clarify\n"
        + "- ask questions\n"
        + "- propose improvements\n"
        + "- help produce a stable version suitable for acceptance."
    )
    if marker == "ROUTE":
        seed += (
            "\n\nGoal: Propose an initial Route (PDO) using the JSON target.\n"
            "Ask concise questions to refine it, then update the JSON.\n"
            "When the user says ready, return JSON only."
        )
    seed_from = (request.POST.get("seed_from") or "").strip().upper()
    seed_from_text = ""
    pref_seed_style, pref_seed_constraints = _get_user_project_seed_defaults(project, request.user)
    seed_style = _normalise_seed_style(
        request.POST.get("seed_style") or request.session.get("rw_seed_style") or pref_seed_style
    )
    seed_constraints = _normalise_seed_constraints(
        request.POST.get("seed_constraints") or request.session.get("rw_seed_constraints") or pref_seed_constraints
    )
    request.session["rw_seed_style"] = seed_style
    request.session["rw_seed_constraints"] = seed_constraints
    request.session.modified = True
    # Backward-compatible guard:
    # if older UI posts ROUTE seed to open-chat, seed ROUTE anchor directly.
    if marker == "ROUTE" and seed_from == "INTENT":
        intent_anchor = ProjectAnchor.objects.filter(project=project, marker="INTENT").first()
        intent_payload = _dict_or_empty(intent_anchor.content_json if intent_anchor else {})
        if not intent_payload and project.defined_cko_id:
            accepted_cko = ProjectCKO.objects.filter(id=project.defined_cko_id, project=project).first()
            intent_payload = _dict_or_empty(accepted_cko.content_json if accepted_cko else {})
        if not intent_payload:
            messages.error(request, "No INTENT source available to seed ROUTE.")
            return redirect(reverse("projects:project_review", args=[project.id]) + "#review-route")

        route_payload = _build_route_seed_from_intent_llm(
            intent_payload=intent_payload,
            user=request.user,
            seed_style=seed_style,
            seed_constraints=seed_constraints,
        )
        used_fallback = False
        if route_payload is None:
            route_payload = _build_route_seed_from_intent(intent_payload)
            used_fallback = True
        route_anchor, _ = ProjectAnchor.objects.get_or_create(
            project=project,
            marker="ROUTE",
            defaults={"content": "", "status": ProjectAnchor.Status.DRAFT},
        )
        before_status = route_anchor.status
        before_content = (route_anchor.content or "")
        before_json = _dict_or_empty(route_anchor.content_json).copy()
        route_anchor.content_json = route_payload
        route_anchor.content = ""
        route_anchor.status = ProjectAnchor.Status.DRAFT
        route_anchor.proposed_by = None
        route_anchor.proposed_at = None
        route_anchor.locked_by = None
        route_anchor.locked_at = None
        route_anchor.last_edited_by = request.user
        route_anchor.last_edited_at = timezone.now()
        route_anchor.save(
            update_fields=[
                "content_json",
                "content",
                "status",
                "proposed_by",
                "proposed_at",
                "locked_by",
                "locked_at",
                "last_edited_by",
                "last_edited_at",
                "updated_at",
            ]
        )
        _record_anchor_audit(
            project=project,
            anchor=route_anchor,
            marker="ROUTE",
            changed_by=request.user,
            change_type="RESEED",
            summary="ROUTE seeded from INTENT anchor.",
            status_before=before_status,
            status_after=route_anchor.status,
            before_content=before_content,
            after_content=route_anchor.content or "",
            before_json=before_json,
            after_json=_dict_or_empty(route_anchor.content_json).copy(),
        )
        if used_fallback:
            messages.warning(request, "Route seed fallback applied. Stage synthesis failed in LLM response.")
        else:
            messages.success(request, "Route seeded from Intent anchor.")
        return redirect(
            reverse("projects:project_review", args=[project.id])
            + "?review_edit=route&review_anchor_open=1#review-route"
        )
    if seed_from == "INTENT":
        anchor = ProjectAnchor.objects.filter(project=project, marker="INTENT").first()
        if anchor and anchor.content_json:
            seed_from_text = build_cko_seed_text(anchor.content_json)
        elif anchor and anchor.content:
            seed_from_text = anchor.content
        elif project.defined_cko_id:
            accepted_cko = ProjectCKO.objects.filter(id=project.defined_cko_id, project=project).first()
            if accepted_cko and isinstance(accepted_cko.content_json, dict) and accepted_cko.content_json:
                seed_from_text = build_cko_seed_text(accepted_cko.content_json)
            elif accepted_cko:
                seed_from_text = (accepted_cko.content_text or "").strip()
    if marker == "EXECUTE":
        ensure_execute_from_route(project)
        route_anchor = ProjectAnchor.objects.filter(project=project, marker="ROUTE").first()
        if route_anchor and isinstance(route_anchor.content_json, dict):
            normalised = normalise_pdo_payload(route_anchor.content_json)
            if normalised:
                seed_from_text = "ROUTE PDO:\n" + json.dumps(normalised, indent=2, ensure_ascii=True)
        execute_anchor = ProjectAnchor.objects.filter(project=project, marker="EXECUTE").first()
        seed = build_execute_conference_seed(project, route_anchor, execute_anchor)
    chat = get_or_create_review_chat(
        project=project,
        user=request.user,
        marker=marker,
        seed_text=seed,
        seed_from_anchor=seed_from_text if seed_from_text else None,
        pdo_target=get_pdo_schema_text() if marker == "ROUTE" else None,
        session_overrides=request.session.get("rw_session_overrides", {}) or {},
    )
    request.session["rw_active_chat_id"] = chat.id
    request.session.modified = True
    return redirect(
        reverse("projects:project_review", args=[project.id])
        + "?review_chat_id="
        + str(chat.id)
        + "&review_chat_open=1#review-"
        + marker.lower()
    )


@require_POST
@login_required
def project_review_route_seed_from_intent(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    wants_json = (
        (request.headers.get("X-Requested-With") or "").lower() == "xmlhttprequest"
        or (request.POST.get("ajax") or "").strip() == "1"
    )

    pref_seed_style, pref_seed_constraints = _get_user_project_seed_defaults(project, request.user)
    seed_style = _normalise_seed_style(
        request.POST.get("seed_style") or request.session.get("rw_seed_style") or pref_seed_style
    )
    seed_constraints = _normalise_seed_constraints(
        request.POST.get("seed_constraints") or request.session.get("rw_seed_constraints") or pref_seed_constraints
    )
    request.session["rw_seed_style"] = seed_style
    request.session["rw_seed_constraints"] = seed_constraints
    request.session.modified = True
    intent_anchor = ProjectAnchor.objects.filter(project=project, marker="INTENT").first()
    intent_payload = _dict_or_empty(intent_anchor.content_json if intent_anchor else {})
    if not intent_payload and project.defined_cko_id:
        accepted_cko = ProjectCKO.objects.filter(id=project.defined_cko_id, project=project).first()
        intent_payload = _dict_or_empty(accepted_cko.content_json if accepted_cko else {})

    if not intent_payload:
        msg = "No INTENT source available to seed ROUTE."
        if wants_json:
            return JsonResponse({"ok": False, "message": msg}, status=400)
        messages.error(request, msg)
        return redirect(reverse("projects:project_review", args=[project.id]) + "#review-route")

    force_llm = (request.POST.get("force_llm") or "").strip() == "1"
    route_payload = _build_route_seed_from_intent_llm(
        intent_payload=intent_payload,
        user=request.user,
        seed_style=seed_style,
        seed_constraints=seed_constraints,
    )
    used_fallback = False
    if route_payload is None:
        if force_llm:
            msg = "Route reseed failed. LLM stage synthesis failed. Existing ROUTE was kept."
            if wants_json:
                return JsonResponse({"ok": False, "message": msg}, status=400)
            messages.error(request, msg)
            return redirect(reverse("projects:project_review", args=[project.id]) + "#review-route")
        route_payload = _build_route_seed_from_intent(intent_payload)
        used_fallback = True
    route_anchor, _ = ProjectAnchor.objects.get_or_create(
        project=project,
        marker="ROUTE",
        defaults={"content": "", "status": ProjectAnchor.Status.DRAFT},
    )
    before_status = route_anchor.status
    before_content = (route_anchor.content or "")
    before_json = _dict_or_empty(route_anchor.content_json).copy()

    route_anchor.content_json = route_payload
    route_anchor.content = ""
    route_anchor.status = ProjectAnchor.Status.DRAFT
    route_anchor.proposed_by = None
    route_anchor.proposed_at = None
    route_anchor.locked_by = None
    route_anchor.locked_at = None
    route_anchor.last_edited_by = request.user
    route_anchor.last_edited_at = timezone.now()
    route_anchor.save(
        update_fields=[
            "content_json",
            "content",
            "status",
            "proposed_by",
            "proposed_at",
            "locked_by",
            "locked_at",
            "last_edited_by",
            "last_edited_at",
            "updated_at",
        ]
    )
    _record_anchor_audit(
        project=project,
        anchor=route_anchor,
        marker="ROUTE",
        changed_by=request.user,
        change_type="RESEED",
        summary="ROUTE seeded from INTENT anchor.",
        status_before=before_status,
        status_after=route_anchor.status,
        before_content=before_content,
        after_content=route_anchor.content or "",
        before_json=before_json,
        after_json=_dict_or_empty(route_anchor.content_json).copy(),
    )
    redirect_url = (
        reverse("projects:project_review", args=[project.id])
        + "?review_edit=route&review_anchor_open=1#review-route"
    )
    if used_fallback:
        msg = "Route seed fallback applied. Stage synthesis failed in LLM response."
        if wants_json:
            return JsonResponse({"ok": True, "message": msg, "redirect_url": redirect_url, "used_fallback": True})
        messages.warning(request, msg)
    else:
        msg = "Route seeded from Intent anchor."
        if wants_json:
            return JsonResponse({"ok": True, "message": msg, "redirect_url": redirect_url, "used_fallback": False})
        messages.success(request, msg)
    return redirect(redirect_url)


@require_POST
@login_required
def project_review_route_restore(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    audit_id_raw = (request.POST.get("audit_id") or "").strip()
    if not audit_id_raw.isdigit():
        messages.error(request, "Choose a route version to restore.")
        return redirect(reverse("projects:project_review", args=[project.id]) + "#review-route")
    audit_row = (
        ProjectAnchorAudit.objects
        .select_related("changed_by")
        .filter(
            project=project,
            marker="ROUTE",
            id=int(audit_id_raw),
            change_type=ProjectAnchorAudit.ChangeType.RESEED,
        )
        .first()
    )
    if not audit_row:
        messages.error(request, "Route version not found.")
        return redirect(reverse("projects:project_review", args=[project.id]) + "#review-route")

    snapshot = (request.POST.get("snapshot") or "before").strip().lower()
    if snapshot not in {"before", "after"}:
        snapshot = "before"
    if snapshot == "after":
        payload_json = _dict_or_empty(audit_row.after_content_json).copy()
        payload_text = (audit_row.after_content or "").strip()
    else:
        payload_json = _dict_or_empty(audit_row.before_content_json).copy()
        payload_text = (audit_row.before_content or "").strip()
    if not payload_json and not payload_text:
        messages.error(request, "Selected route version is empty.")
        return redirect(reverse("projects:project_review", args=[project.id]) + "#review-route")

    route_anchor, _ = ProjectAnchor.objects.get_or_create(
        project=project,
        marker="ROUTE",
        defaults={"content": "", "status": ProjectAnchor.Status.DRAFT},
    )
    before_status = route_anchor.status
    before_content = (route_anchor.content or "")
    before_json = _dict_or_empty(route_anchor.content_json).copy()

    route_anchor.content_json = payload_json
    route_anchor.content = payload_text
    route_anchor.status = ProjectAnchor.Status.DRAFT
    route_anchor.proposed_by = None
    route_anchor.proposed_at = None
    route_anchor.locked_by = None
    route_anchor.locked_at = None
    route_anchor.last_edited_by = request.user
    route_anchor.last_edited_at = timezone.now()
    route_anchor.save(
        update_fields=[
            "content_json",
            "content",
            "status",
            "proposed_by",
            "proposed_at",
            "locked_by",
            "locked_at",
            "last_edited_by",
            "last_edited_at",
            "updated_at",
        ]
    )
    _record_anchor_audit(
        project=project,
        anchor=route_anchor,
        marker="ROUTE",
        changed_by=request.user,
        change_type=ProjectAnchorAudit.ChangeType.UPDATE,
        summary=f"ROUTE restored from version {audit_row.id} ({snapshot}).",
        status_before=before_status,
        status_after=route_anchor.status,
        before_content=before_content,
        after_content=route_anchor.content or "",
        before_json=before_json,
        after_json=_dict_or_empty(route_anchor.content_json).copy(),
    )
    messages.success(request, "Route version restored.")
    return redirect(
        reverse("projects:project_review", args=[project.id])
        + "?review_edit=route&review_anchor_open=1#review-route"
    )


@require_POST
@login_required
def project_review_intent_seed(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    pref_seed_style, pref_seed_constraints = _get_user_project_seed_defaults(project, request.user)
    seed_style = _normalise_seed_style(
        request.POST.get("seed_style") or request.session.get("rw_seed_style") or pref_seed_style
    )
    seed_constraints = _normalise_seed_constraints(
        request.POST.get("seed_constraints") or request.session.get("rw_seed_constraints") or pref_seed_constraints
    )
    request.session["rw_seed_style"] = seed_style
    request.session["rw_seed_constraints"] = seed_constraints
    request.session.modified = True
    intent_anchor = ProjectAnchor.objects.filter(project=project, marker="INTENT").first()
    accepted_cko = None
    if project.defined_cko_id:
        accepted_cko = ProjectCKO.objects.filter(id=project.defined_cko_id, project=project).first()

    source_payload = _dict_or_empty(intent_anchor.content_json if intent_anchor else {})
    if not source_payload and accepted_cko:
        source_payload = _dict_or_empty(accepted_cko.content_json)
    if not source_payload:
        messages.error(request, "No INTENT source available to seed.")
        return redirect(reverse("projects:project_review", args=[project.id]) + "#review-intent")

    default_provenance = "Seeded from accepted CKO on " + timezone.now().strftime("%Y-%m-%d")
    llm_payload = _build_intent_seed_from_cko_llm(
        source_payload=source_payload,
        user=request.user,
        seed_style=seed_style,
        seed_constraints=seed_constraints,
    )
    payload = _normalise_intent_payload(llm_payload or source_payload, default_provenance=default_provenance)
    used_llm = llm_payload is not None

    anchor, _ = ProjectAnchor.objects.get_or_create(
        project=project,
        marker="INTENT",
        defaults={"content": "", "status": ProjectAnchor.Status.DRAFT},
    )
    before_status = anchor.status
    before_content = (anchor.content or "")
    before_json = _dict_or_empty(anchor.content_json).copy()
    anchor.content_json = payload
    anchor.content = ""
    anchor.status = ProjectAnchor.Status.DRAFT
    anchor.proposed_by = None
    anchor.proposed_at = None
    anchor.locked_by = None
    anchor.locked_at = None
    anchor.last_edited_by = request.user
    anchor.last_edited_at = timezone.now()
    anchor.save(
        update_fields=[
            "content_json",
            "content",
            "status",
            "proposed_by",
            "proposed_at",
            "locked_by",
            "locked_at",
            "last_edited_by",
            "last_edited_at",
            "updated_at",
        ]
    )
    _record_anchor_audit(
        project=project,
        anchor=anchor,
        marker="INTENT",
        changed_by=request.user,
        change_type="RESEED",
        summary="INTENT seeded from accepted CKO.",
        status_before=before_status,
        status_after=anchor.status,
        before_content=before_content,
        after_content=anchor.content or "",
        before_json=before_json,
        after_json=_dict_or_empty(anchor.content_json).copy(),
    )
    if used_llm:
        messages.success(request, "INTENT seeded with LLM enrichment.")
    else:
        messages.warning(request, "INTENT seeded from source. LLM enrichment unavailable.")
    return redirect(
        reverse("projects:project_review", args=[project.id])
        + "?review_edit=intent&review_anchor_open=1#review-intent"
    )


@require_POST
@login_required
def project_review_stage_chat_open(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    wants_json = (
        (request.headers.get("X-Requested-With") or "").lower() == "xmlhttprequest"
        or (request.POST.get("ajax") or "").strip() == "1"
    )
    marker = (request.POST.get("marker") or "").strip().upper()
    stage_raw = (request.POST.get("stage_number") or "").strip()
    stage_id = (request.POST.get("stage_id") or "").strip()
    if not stage_raw.isdigit():
        if wants_json:
            return JsonResponse({"ok": False, "message": "Invalid stage number."}, status=400)
        messages.error(request, "Invalid stage number.")
        return redirect("projects:project_review", project_id=project.id)
    stage_number = int(stage_raw)
    if marker == "EXECUTE":
        ensure_execute_from_route(project)

    anchor = ProjectAnchor.objects.filter(project=project, marker=marker).first()
    stage_payload = {}
    if anchor and isinstance(anchor.content_json, dict):
        normalised = anchor.content_json if marker == "EXECUTE" else normalise_pdo_payload(anchor.content_json)
        for item in normalised.get("stages", []):
            if stage_id and str(item.get("stage_id") or "").strip() == stage_id:
                stage_payload = item
                stage_number = int(item.get("stage_number") or stage_number)
                break
            try:
                if int(item.get("stage_number") or 0) == stage_number:
                    stage_payload = item
                    break
            except Exception:
                continue
    seed = "Stage context:\n" + json.dumps(stage_payload or {}, indent=2, ensure_ascii=True)
    if marker == "EXECUTE":
        route_anchor = ProjectAnchor.objects.filter(project=project, marker="ROUTE").first()
        execute_anchor = ProjectAnchor.objects.filter(project=project, marker="EXECUTE").first()
        route_stage = {}
        if route_anchor and isinstance(route_anchor.content_json, dict):
            normalised_route = normalise_pdo_payload(route_anchor.content_json)
            for item in normalised_route.get("stages", []):
                if stage_id and str(item.get("stage_id") or "").strip() == stage_id:
                    route_stage = item
                    break
                try:
                    if int(item.get("stage_number") or 0) == stage_number:
                        route_stage = item
                        break
                except Exception:
                    continue
        seed = build_execute_stage_seed(
            project,
            stage_id or str(stage_number),
            route_anchor,
            execute_anchor,
            stage_payload,
            route_stage,
        )
    chat = get_or_create_review_stage_chat(
        project=project,
        user=request.user,
        marker=marker,
        stage_number=stage_number,
        seed_text=seed,
        session_overrides=request.session.get("rw_session_overrides", {}) or {},
    )
    request.session["rw_active_chat_id"] = chat.id
    request.session.modified = True
    redirect_url = (
        reverse("projects:project_review", args=[project.id])
        + "?review_chat_id="
        + str(chat.id)
        + "&review_chat_open=1#review-"
        + marker.lower()
    )
    if wants_json:
        ctx = build_chat_turn_context(request, chat)
        qs = request.GET.copy()
        qs["review_chat_id"] = str(chat.id)
        qs["review_chat_open"] = "1"
        qs.pop("turn", None)
        qs.pop("system", None)
        ctx["chat"] = chat
        ctx["qs_base"] = qs.urlencode()
        ctx["is_open"] = True
        drawer_html = render_to_string("projects/review_inline_chat.html", {"chat_ctx": ctx}, request=request)
        return JsonResponse(
            {
                "ok": True,
                "chat_id": chat.id,
                "marker": marker,
                "stage_number": stage_number,
                "stage_id": stage_id,
                "redirect_url": redirect_url,
                "drawer_html": drawer_html,
            }
        )
    return redirect(
        redirect_url
    )


@require_POST
@login_required
def project_review_anchor_status(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    marker = (request.POST.get("marker") or "").strip().upper()
    action = (request.POST.get("action") or "").strip().lower()
    markers = {m[0] for m in MARKERS}
    if marker not in markers:
        messages.error(request, "Unknown review marker.")
        return redirect("projects:project_review", project_id=project.id)

    anchor, _ = ProjectAnchor.objects.get_or_create(
        project=project,
        marker=marker,
        defaults={"content": "", "status": ProjectAnchor.Status.DRAFT},
    )
    before_status = anchor.status
    before_content = (anchor.content or "")
    before_json = _dict_or_empty(anchor.content_json).copy()

    now = timezone.now()
    if action == "propose":
        if anchor.status != ProjectAnchor.Status.DRAFT:
            messages.error(request, "Only draft anchors can be proposed.")
        else:
            anchor.status = ProjectAnchor.Status.PROPOSED
            anchor.proposed_by = request.user
            anchor.proposed_at = now
            anchor.save(update_fields=["status", "proposed_by", "proposed_at", "updated_at"])
            _record_anchor_audit(
                project=project,
                anchor=anchor,
                marker=marker,
                changed_by=request.user,
                change_type="STATUS",
                summary=marker + " status changed to PROPOSED.",
                status_before=before_status,
                status_after=anchor.status,
                before_content=before_content,
                after_content=anchor.content or "",
                before_json=before_json,
                after_json=_dict_or_empty(anchor.content_json).copy(),
            )
            messages.success(request, "Anchor proposed.")
    elif action == "lock":
        if anchor.status != ProjectAnchor.Status.PROPOSED:
            messages.error(request, "Only proposed anchors can be locked.")
        else:
            anchor.status = ProjectAnchor.Status.PASS_LOCKED
            anchor.locked_by = request.user
            anchor.locked_at = now
            anchor.save(update_fields=["status", "locked_by", "locked_at", "updated_at"])
            _record_anchor_audit(
                project=project,
                anchor=anchor,
                marker=marker,
                changed_by=request.user,
                change_type="STATUS",
                summary=marker + " status changed to PASS_LOCKED.",
                status_before=before_status,
                status_after=anchor.status,
                before_content=before_content,
                after_content=anchor.content or "",
                before_json=before_json,
                after_json=_dict_or_empty(anchor.content_json).copy(),
            )
            messages.success(request, "Anchor locked.")
    elif action == "reopen":
        if anchor.status != ProjectAnchor.Status.PASS_LOCKED:
            messages.error(request, "Only locked anchors can be reopened.")
        else:
            anchor.status = ProjectAnchor.Status.DRAFT
            anchor.proposed_by = None
            anchor.proposed_at = None
            anchor.locked_by = None
            anchor.locked_at = None
            anchor.save(update_fields=[
                "status",
                "proposed_by",
                "proposed_at",
                "locked_by",
                "locked_at",
                "updated_at",
            ])
            _record_anchor_audit(
                project=project,
                anchor=anchor,
                marker=marker,
                changed_by=request.user,
                change_type="STATUS",
                summary=marker + " status changed to DRAFT.",
                status_before=before_status,
                status_after=anchor.status,
                before_content=before_content,
                after_content=anchor.content or "",
                before_json=before_json,
                after_json=_dict_or_empty(anchor.content_json).copy(),
            )
            messages.success(request, "Anchor reopened.")
    else:
        messages.error(request, "Unknown action.")

    return redirect(
        reverse("projects:project_review", args=[project.id])
        + "#review-"
        + marker.lower()
    )


@require_POST
@login_required
def project_review_execute_reseed(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    pref_seed_style, pref_seed_constraints = _get_user_project_seed_defaults(project, request.user)
    seed_style = _normalise_seed_style(
        request.POST.get("seed_style") or request.session.get("rw_seed_style") or pref_seed_style
    )
    seed_constraints = _normalise_seed_constraints(
        request.POST.get("seed_constraints") or request.session.get("rw_seed_constraints") or pref_seed_constraints
    )
    request.session["rw_seed_style"] = seed_style
    request.session["rw_seed_constraints"] = seed_constraints
    request.session.modified = True
    route_anchor = ProjectAnchor.objects.filter(project=project, marker="ROUTE").first()
    route_payload = _dict_or_empty(route_anchor.content_json if route_anchor else {})
    before_anchor = ProjectAnchor.objects.filter(project=project, marker="EXECUTE").first()
    before_content = (before_anchor.content if before_anchor else "") or ""
    before_json = _dict_or_empty(before_anchor.content_json if before_anchor else {}).copy()
    before_status = (before_anchor.status if before_anchor else "") or ""
    reseeded = reseed_execute_from_route(project)
    if not reseeded:
        messages.error(request, "No ROUTE anchor available to reseed EXECUTE.")
    else:
        after_anchor = ProjectAnchor.objects.filter(project=project, marker="EXECUTE").first()
        llm_actions = _build_execute_stage_actions_llm(
            route_payload=route_payload,
            user=request.user,
            seed_style=seed_style,
            seed_constraints=seed_constraints,
        )
        used_llm = False
        if after_anchor and isinstance(after_anchor.content_json, dict):
            payload = dict(after_anchor.content_json)
            stage_rows = payload.get("stages") if isinstance(payload.get("stages"), list) else []
            if llm_actions and stage_rows:
                for item in stage_rows:
                    if not isinstance(item, dict):
                        continue
                    sid = str(item.get("stage_id") or "").strip()
                    if not sid or sid not in llm_actions:
                        continue
                    item["next_actions"] = llm_actions[sid].get("next_actions") or item.get("next_actions") or ""
                    item["notes"] = llm_actions[sid].get("notes") or item.get("notes") or ""
                payload["stages"] = stage_rows
                after_anchor.content_json = payload
                after_anchor.content = ""
                after_anchor.save(update_fields=["content_json", "content", "updated_at"])
                used_llm = True
        if after_anchor:
            _record_anchor_audit(
                project=project,
                anchor=after_anchor,
                marker="EXECUTE",
                changed_by=request.user,
                change_type="RESEED",
                summary="EXECUTE reseeded from ROUTE.",
                status_before=before_status,
                status_after=after_anchor.status,
                before_content=before_content,
                after_content=(after_anchor.content or ""),
                before_json=before_json,
                after_json=_dict_or_empty(after_anchor.content_json).copy(),
            )
        if used_llm:
            messages.success(request, "EXECUTE reseeded from ROUTE with LLM stage actions.")
        else:
            messages.warning(request, "EXECUTE reseeded from ROUTE. LLM stage action synthesis was unavailable.")
    return redirect(reverse("projects:project_review", args=[project.id]) + "#review-execute")
