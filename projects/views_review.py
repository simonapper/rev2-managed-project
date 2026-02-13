from __future__ import annotations

import json
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from chats.models import ChatWorkspace
from chats.services.turns import build_chat_turn_context
from projects.models import ProjectAnchor, ProjectAnchorAudit, ProjectReviewChat, ProjectReviewStageChat
from projects.services_project_membership import accessible_projects_qs
from projects.services.artefact_render import render_artefact_html
from projects.services_artefacts import (
    build_cko_seed_text,
    get_pdo_schema_text,
    merge_execute_payload,
    normalise_pdo_payload,
    seed_execute_from_route,
)
from projects.services_execute import seed_execute_from_route as ensure_execute_from_route, merge_execute_from_route
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
    intent_locked = bool(intent_anchor and intent_anchor.status == ProjectAnchor.Status.PASS_LOCKED)

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
            }
        )

    review_edit = (request.GET.get("review_edit") or "").strip().lower()
    review_anchor_open = (request.GET.get("review_anchor_open") or "").strip().lower() in ("1", "true", "yes")
    return render(
        request,
        "projects/project_review.html",
        {
            "project": project,
            "sections": sections,
            "review_edit": review_edit,
            "review_anchor_open": review_anchor_open,
        },
    )


@login_required
def project_review_print_intent(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    anchor = ProjectAnchor.objects.filter(project=project, marker="INTENT").first()

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

    return render(
        request,
        "projects/review_print_intent.html",
        {
            "project": project,
            "anchor": anchor,
            "content_text": content_text,
            "content_html": content_html,
        },
    )


@login_required
def project_review_print_route(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    anchor = ProjectAnchor.objects.filter(project=project, marker="ROUTE").first()

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
    content_html = ""
    if normalised:
        content_html = render_artefact_html("PDO", normalised)

    return render(
        request,
        "projects/review_print_route.html",
        {
            "project": project,
            "anchor": anchor,
            "content_text": content_text,
            "content_html": content_html,
        },
    )


@require_POST
@login_required
def project_review_anchor_update(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
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
        if any(v for v in route_fields.values()) or stages:
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
    if seed_from == "INTENT":
        anchor = ProjectAnchor.objects.filter(project=project, marker="INTENT").first()
        if anchor and anchor.content_json:
            seed_from_text = build_cko_seed_text(anchor.content_json)
        elif anchor and anchor.content:
            seed_from_text = anchor.content
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
def project_review_stage_chat_open(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    marker = (request.POST.get("marker") or "").strip().upper()
    stage_raw = (request.POST.get("stage_number") or "").strip()
    stage_id = (request.POST.get("stage_id") or "").strip()
    if not stage_raw.isdigit():
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
    return redirect(
        reverse("projects:project_review", args=[project.id])
        + "?review_chat_id="
        + str(chat.id)
        + "&review_chat_open=1#review-"
        + marker.lower()
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
    before_anchor = ProjectAnchor.objects.filter(project=project, marker="EXECUTE").first()
    before_content = (before_anchor.content if before_anchor else "") or ""
    before_json = _dict_or_empty(before_anchor.content_json if before_anchor else {}).copy()
    before_status = (before_anchor.status if before_anchor else "") or ""
    merged = merge_execute_from_route(project)
    if not merged:
        messages.error(request, "No ROUTE anchor available to reseed EXECUTE.")
    else:
        after_anchor = ProjectAnchor.objects.filter(project=project, marker="EXECUTE").first()
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
        messages.success(request, "EXECUTE reseeded from ROUTE (non-destructive).")
    return redirect(reverse("projects:project_review", args=[project.id]) + "#review-execute")
