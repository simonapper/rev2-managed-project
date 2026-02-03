# -*- coding: utf-8 -*-
# projects/views_pde_ui.py
#
# PDE v1 - Minimal UI: seed -> draft -> validate/lock -> commit.
# NOTE: Keep code comments 7-bit ASCII only.

from __future__ import annotations

from typing import Any, Dict, List

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django.urls import reverse

from chats.services.llm import generate_panes
from projects.models import Project, ProjectDefinitionField
from projects.services.pde import draft_pde_from_seed
from projects.services.pde_loop import ensure_pde_fields, run_pde_controlled
from projects.services.pde_spec import PDE_REQUIRED_FIELDS



def _can_edit_pde(user, project: Project) -> bool:
    # Minimal guard: owner only. Extend later with memberships.
    return bool(project.owner_id == getattr(user, "id", None))


def _fields_for_project(project: Project) -> Dict[str, ProjectDefinitionField]:
    qs = ProjectDefinitionField.objects.filter(project=project)
    out: Dict[str, ProjectDefinitionField] = {}
    for row in qs:
        k = (row.field_key or "").strip()
        if k:
            out[k] = row
    return out


def _compute_pde_state(project: Project) -> Dict[str, Any]:
    rows = ProjectDefinitionField.objects.filter(project=project)
    any_locked = rows.filter(status=ProjectDefinitionField.Status.PASS_LOCKED).exists()

    # "all locked" here means all L1-MUST fields locked.
    must_keys = [s.key for s in PDE_REQUIRED_FIELDS if (getattr(s, "tier", "") or "") == "L1-MUST"]
    locked_must = 0
    if must_keys:
        locked_must = ProjectDefinitionField.objects.filter(
            project=project,
            field_key__in=must_keys,
            status=ProjectDefinitionField.Status.PASS_LOCKED,
        ).count()
    all_locked = bool(must_keys) and locked_must == len(must_keys)

    if all_locked:
        badge = {"text": "Ready to Commit", "class": "bg-success"}
    elif any_locked:
        badge = {"text": "Partially Locked", "class": "bg-warning text-dark"}
    else:
        badge = {"text": "Draft", "class": "bg-secondary"}

    return {"any_locked": any_locked, "all_locked": all_locked, "badge": badge}


def _pde_nav(specs: List[Dict[str, Any]]) -> Dict[str, Any]:
    items: List[Dict[str, str]] = []
    for f in specs:
        st = (f.get("status") or "").strip()
        if st == ProjectDefinitionField.Status.PASS_LOCKED:
            dot = "#198754"  # green
        elif st:
            dot = "#ffc107"  # amber
        else:
            dot = "#adb5bd"  # grey
        items.append(
            {
                "label": f.get("label") or f.get("key") or "",
                "anchor": f.get("key") or "",
                "dot": dot,
            }
        )
    return {"items": items}

def _help_log_session_key(project_id: int) -> str:
    return "pde_help_log_" + str(project_id)

def _get_help_log(request, project_id: int) -> List[Dict[str, str]]:
    key = _help_log_session_key(project_id)
    raw = request.session.get(key, []) or []
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, str]] = []
    for x in raw:
        if isinstance(x, dict) and "role" in x and "text" in x:
            out.append({"role": str(x["role"]), "text": str(x["text"])})
    return out[-30:]  # keep last 30

def _set_help_log(request, project_id: int, log: List[Dict[str, str]]) -> None:
    key = _help_log_session_key(project_id)
    request.session[key] = (log or [])[-30:]
    request.session.modified = True

def _pde_help_answer(*, question: str, project: Project, generate_panes_func) -> str:
    q = (question or "").strip()
    if not q:
        return "Ask a specific question and I will help."

    system_blocks = [
        "You are a PDE help assistant.\n"
        "- Explain intent and meaning of PDE fields and validation results.\n"
        "- Keep answers short and concrete.\n"
        "- Do NOT edit project fields.\n"
        "- Do NOT invent facts.\n"
    ]

    user_text = (
        "Project: " + (project.name or "") + "\n"
        "Question:\n" + q
    )

    panes = generate_panes_func(
        user_text,
        image_parts=None,
        system_blocks=system_blocks,
        force_model="gpt-5.1",
    )
    return (str(panes.get("output") or "")).strip() or "No answer returned."



@login_required
@require_http_methods(["GET", "POST"])
def pde_detail(request, project_id: int):
    project = get_object_or_404(Project, id=project_id)

    if not _can_edit_pde(request.user, project):
        messages.error(request, "You do not have permission to edit this project definition.")
        return redirect("accounts:dashboard")

    # Ensure PDE rows exist
    ensure_pde_fields(project)

    action = (request.POST.get("action") or "").strip().lower()

    # Seed text: default to project.purpose for convenience
    seed_text = (project.purpose or "").strip()
    if request.method == "POST" and action in ("draft",):
        seed_text = (request.POST.get("seed_text") or "").strip() or seed_text


    # ------------------------------------------------------------
    # Action: Save and Exit (no validation)
    # ------------------------------------------------------------
    if request.method == "POST" and action == "save_exit":      
        request.session.pop("pde_help_auto_open_" + str(project.id), None)
        request.session.modified = True
        for spec in PDE_REQUIRED_FIELDS:
            proposed = (request.POST.get(spec.key) or "").strip()
            row = ProjectDefinitionField.objects.get(project=project, field_key=spec.key)
            row.value_text = proposed  # allow clearing
            if row.status != ProjectDefinitionField.Status.PASS_LOCKED:
                row.status = ProjectDefinitionField.Status.PROPOSED
            row.save(update_fields=["value_text", "status", "updated_at"])

        messages.success(request, "Draft saved.")
        return redirect("accounts:dashboard")

    # ------------------------------------------------------------
    # Action: Help chat (non-canonical)
    # ------------------------------------------------------------
    if request.method == "POST" and action == "help_clear":
        _set_help_log(request, project.id, [])
        messages.success(request, "Help chat cleared.")
        return redirect("projects:pde_detail", project_id=project.id)

    if request.method == "POST" and action == "help_ask":
        q = (request.POST.get("help_question") or "").strip()
        log = _get_help_log(request, project.id)
        if q:
            log.append({"role": "user", "text": q})
            a = _pde_help_answer(question=q, project=project, generate_panes_func=generate_panes)
            log.append({"role": "assistant", "text": a})
            _set_help_log(request, project.id, log)
        else:
            messages.error(request, "Type a question first.")
        # Reopen help drawer after refresh.
        request.session["pde_help_auto_open_" + str(project.id)] = True
        request.session.modified = True
        return redirect("projects:pde_detail", project_id=project.id)


    # ------------------------------------------------------------
    # Action: Draft from seed (fills draft values, does not lock)
    # ------------------------------------------------------------
    if request.method == "POST" and action == "draft":
        res = draft_pde_from_seed(generate_panes_func=generate_panes, seed_text=seed_text)
        if not res.get("ok"):
            messages.error(request, "Draft failed: " + str(res.get("error") or "unknown error"))
        else:
            fields = (((res.get("draft") or {}).get("hypotheses") or {}).get("fields") or {})
            if not isinstance(fields, dict):
                fields = {}

            updated = 0
            for spec in PDE_REQUIRED_FIELDS:
                v = (fields.get(spec.key) or "").strip()
                if not v:
                    continue
                row = ProjectDefinitionField.objects.get(project=project, field_key=spec.key)
                row.value_text = v
                row.status = ProjectDefinitionField.Status.PROPOSED
                row.last_validation = {}
                row.save(update_fields=["value_text", "status", "last_validation", "updated_at"])
                updated += 1

            messages.success(request, "Draft created. Updated fields: " + str(updated))

        return redirect("projects:pde_detail", project_id=project.id)

    # ------------------------------------------------------------
    # Action: Validate + lock (controlled loop)
    # ------------------------------------------------------------
    if request.method == "POST" and action == "validate_lock":
        user_inputs: Dict[str, str] = {}
        for spec in PDE_REQUIRED_FIELDS:
            user_inputs[spec.key] = (request.POST.get(spec.key) or "").strip()

        res = run_pde_controlled(
            project=project,
            user=request.user,
            generate_panes_func=generate_panes,
            user_inputs=user_inputs,
        )

        if res.get("ok"):
            messages.success(request, "All fields locked. You can now Commit to DEFINED.")
        else:
            fb = res.get("first_blocker") or {}
            messages.error(
                request,
                "Blocked at: " + str(fb.get("field_key") or "") + " (" + str(fb.get("verdict") or "") + ")",
            )

        return redirect("projects:pde_detail", project_id=project.id)

    # ------------------------------------------------------------
    # Action: Commit (create DRAFT CKO) -> Preview
    # ------------------------------------------------------------

    if request.method == "POST" and action == "commit":
        state = _compute_pde_state(project)
        if not state.get("all_locked"):
            messages.error(request, "Cannot commit: not all required fields are locked.")
            return redirect("projects:pde_detail", project_id=project.id)

        # Create DRAFT ProjectCKO snapshot (no acceptance here).
        res = commit_project_definition(project=project)
        if not res.get("ok"):
            messages.error(request, "Commit failed: " + str(res.get("error") or "unknown error"))
            return redirect("projects:pde_detail", project_id=project.id)

        cko_id = res.get("cko_id")
        if not cko_id:
            messages.error(request, "Commit failed: missing cko_id.")
            return redirect("projects:pde_detail", project_id=project.id)

        return redirect("projects:cko_preview", project_id=project.id)

    # ------------------------------------------------------------
    # Render UI
    # ------------------------------------------------------------
    rows = _fields_for_project(project)

    specs: List[Dict[str, Any]] = []
    for spec in PDE_REQUIRED_FIELDS:
        row = rows.get(spec.key)
        specs.append(
            {
                "key": spec.key,
                "label": spec.label,
                "tier": getattr(spec, "tier", ""),
                "required": getattr(spec, "required", True),
                "status": (getattr(row, "status", "") or "") if row else "",
                "value_text": (getattr(row, "value_text", "") or "") if row else "",
                "last_validation": getattr(row, "last_validation", {}) if row else {},
                "summary": getattr(spec, "summary", ""),
                "help_text": getattr(spec, "help_text", ""),

            }
        )
    current_field_key = ""
    for s in specs:
        if (s.get("status") or "") != ProjectDefinitionField.Status.PASS_LOCKED:
            current_field_key = s.get("key") or ""
            break


    state = _compute_pde_state(project)
    pde_help_log = _get_help_log(request, project.id)
    auto_key = "pde_help_auto_open_" + str(project.id)
    pde_help_auto_open = bool(request.session.get(auto_key))
    if pde_help_auto_open:
        request.session.pop(auto_key, None)
        request.session.modified = True

    return render(
        request,
        "projects/pde_detail.html",
        {
            "project": project,
            "seed_text": seed_text,
            "specs": specs,
            "pde_status_badge": state["badge"],
            "pde_nav": _pde_nav(specs),
            "any_locked": state["any_locked"],
            "all_locked": state["all_locked"],
            "pde_help_log": pde_help_log,
            "pde_help_auto_open": pde_help_auto_open,
            "ui_return_to": reverse("accounts:dashboard"),
            "current_field_key": current_field_key,

            # Global topbar Help offcanvas wiring (PDE uses existing help log)
            "rw_help_enabled": True,
            "rw_help_title": "PDE Help",
            "rw_help_hint": "Ask questions about the Project Definition.",
            "rw_help_post_url": request.path,
            "rw_help_log": pde_help_log,
            "rw_help_auto_open": pde_help_auto_open,
        },
    )

