# -*- coding: utf-8 -*-

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from projects.models import WorkItem
from projects.services_project_membership import accessible_projects_qs
from chats.services.contracts.phase_resolver import resolve_phase_contract
from chats.services.contracts.pipeline import ContractContext


def _latest_seed_text(work_item: WorkItem) -> str:
    active_rev = int(work_item.active_seed_revision or 0)
    if active_rev <= 0:
        return ""
    for item in list(work_item.seed_log or []):
        if not isinstance(item, dict):
            continue
        if int(item.get("revision") or 0) == active_rev:
            return str(item.get("seed_text") or "").strip()
    return ""


def _normalise_seed_status(raw: str) -> str:
    status = str(raw or "").strip().upper()
    if status == WorkItem.SEED_STATUS_ACTIVE:
        return WorkItem.SEED_STATUS_DRAFT
    if status in {
        WorkItem.SEED_STATUS_DRAFT,
        WorkItem.SEED_STATUS_PROPOSED,
        WorkItem.SEED_STATUS_PASS_LOCKED,
        WorkItem.SEED_STATUS_RETIRED,
    }:
        return status
    return WorkItem.SEED_STATUS_DRAFT


@login_required
def work_item_detail(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    work_item = (
        WorkItem.objects
        .filter(project=project)
        .order_by("-updated_at", "-id")
        .first()
    )
    if work_item is None:
        work_item = WorkItem.create_minimal(project=project)

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "propose_seed":
            seed_text = (request.POST.get("seed_text") or "").strip()
            reason = (request.POST.get("reason") or "").strip()
            if not seed_text:
                messages.error(request, "Seed text is required.")
            else:
                work_item.append_seed_revision(seed_text=seed_text, created_by=request.user, reason=reason)
                messages.success(request, "Seed proposed.")
            return redirect("projects:work_item_detail", project_id=project.id)

        if action == "lock_seed":
            revision_raw = (request.POST.get("revision_number") or "").strip()
            try:
                work_item.lock_seed(int(revision_raw))
                messages.success(request, "Seed locked.")
            except Exception as exc:
                messages.error(request, str(exc))
            return redirect("projects:work_item_detail", project_id=project.id)

        if action == "rollback_seed":
            revision_raw = (request.POST.get("revision_number") or "").strip()
            try:
                work_item.rollback_to(int(revision_raw))
                messages.success(request, "Rollback appended.")
            except Exception as exc:
                messages.error(request, str(exc))
            return redirect("projects:work_item_detail", project_id=project.id)

        if action == "advance_phase":
            to_phase = (request.POST.get("to_phase") or "").strip().upper()
            try:
                work_item.set_phase(to_phase)
                messages.success(request, "Phase updated.")
            except Exception as exc:
                messages.error(request, str(exc))
            return redirect("projects:work_item_detail", project_id=project.id)

        if action == "save_derax_endpoint":
            spec_text = request.POST.get("derax_endpoint_spec") or ""
            try:
                work_item.set_derax_endpoint(spec_text, actor=request.user, lock=False)
                messages.success(request, "DERAX endpoint saved as draft.")
            except Exception as exc:
                messages.error(request, str(exc))
            return redirect("projects:work_item_detail", project_id=project.id)

        if action == "lock_derax_endpoint":
            spec_text = request.POST.get("derax_endpoint_spec") or ""
            try:
                if str(spec_text).strip():
                    work_item.set_derax_endpoint(spec_text, actor=request.user, lock=True)
                else:
                    work_item.lock_derax_endpoint(actor=request.user)
                messages.success(request, "DERAX endpoint locked.")
            except Exception as exc:
                messages.error(request, str(exc))
            return redirect("projects:work_item_detail", project_id=project.id)

    seed_history = []
    for item in reversed(list(work_item.seed_log or [])):
        if not isinstance(item, dict):
            continue
        revision = int(item.get("revision") or 0)
        seed_history.append(
            {
                "revision": revision,
                "status": _normalise_seed_status(item.get("status")),
                "seed_text": str(item.get("seed_text") or ""),
                "reason": str(item.get("reason") or ""),
                "created_at": str(item.get("created_at") or ""),
                "is_active": revision == int(work_item.active_seed_revision or 0),
            }
        )

    phase_options = []
    for phase in WorkItem.ALLOWED_PHASES:
        if phase == (work_item.active_phase or "").strip().upper():
            continue
        phase_options.append(
            {
                "phase": phase,
                "allowed": work_item.can_transition(phase),
            }
        )
    phase_resolution = resolve_phase_contract(ContractContext(work_item=work_item))
    active_source = str(getattr(phase_resolution, "source", "") or "")
    active_key = str(getattr(phase_resolution, "effective_phase_contract", "") or "")
    if active_source == "workitem":
        active_source_label = "WorkItem phase"
    elif active_source == "ppde":
        active_source_label = "PPDE contract"
    else:
        active_source_label = active_source or "unknown"

    return render(
        request,
        "projects/work_item_detail.html",
        {
            "project": project,
            "work_item": work_item,
            "work_item_title": work_item.title or f"WorkItem {work_item.id}",
            "latest_seed_text": _latest_seed_text(work_item),
            "seed_history": seed_history,
            "phase_options": phase_options,
            "active_contract_source": active_source_label,
            "active_contract_key": active_key,
        },
    )


@login_required
def work_item_export(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    work_item = (
        WorkItem.objects
        .filter(project=project)
        .order_by("-updated_at", "-id")
        .first()
    )
    if work_item is None:
        work_item = WorkItem.create_minimal(project=project)

    payload = {
        "project_id": project.id,
        "work_item": {
            "id": work_item.id,
            "title": work_item.title or f"WorkItem {work_item.id}",
            "intent_raw": work_item.intent_raw,
            "state": work_item.state,
            "active_phase": work_item.active_phase,
            "active_seed_revision": work_item.active_seed_revision,
            "seed_log": list(work_item.seed_log or []),
            "deliverables": list(work_item.deliverables or []),
            "derax_endpoint_spec": work_item.derax_endpoint_spec,
            "derax_endpoint_locked": bool(work_item.derax_endpoint_locked),
            "activity_log": list(work_item.activity_log or []),
            "created_at": work_item.created_at.isoformat() if work_item.created_at else "",
            "updated_at": work_item.updated_at.isoformat() if work_item.updated_at else "",
        },
    }
    body = json.dumps(payload, ensure_ascii=True, indent=2)
    filename = f"work_item_{work_item.id}_export.json"
    response = HttpResponse(body, content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
