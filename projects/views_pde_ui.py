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
from django.views.decorators.http import require_http_methods, require_POST
from django.urls import reverse
from django.utils import timezone

from chats.services.llm import generate_panes
from projects.models import Project, ProjectDefinitionField, ProjectCKO, ProjectTopicChat
from projects.services.pde import draft_pde_from_seed, validate_field
from projects.services.pde_commit import commit_project_definition
from projects.services.pde_required_keys import pde_required_keys_for_defined
from projects.services.pde_loop import ensure_pde_fields, read_locked_fields
from projects.services.pde_spec import PDE_REQUIRED_FIELDS
from projects.services_topic_chat import get_or_create_topic_chat
from projects.services_project_membership import can_edit_pde, is_project_committer
from chats.services.turns import build_chat_turn_context


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
    print("PDE_STATE project", project.id, "must_keys", len(must_keys), "locked_must", locked_must, "all_locked", all_locked)


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
    )
    return (str(panes.get("output") or "")).strip() or "No answer returned."


@require_POST
@login_required
def pde_topic_chat_open(request, project_id: int):
    project = get_object_or_404(Project, pk=project_id)
    if not can_edit_pde(project, request.user):
        messages.error(request, "You do not have permission to open a topic chat for this section.")
        return redirect("projects:pde_detail", project_id=project.id)

    spec_key = (request.POST.get("spec_key") or "").strip()
    if not spec_key:
        messages.error(request, "Invalid field.")
        return redirect("projects:pde_detail", project_id=project.id)

    spec_map = {s.key: s for s in PDE_REQUIRED_FIELDS}
    spec = spec_map.get(spec_key)
    if not spec:
        messages.error(request, "Field not found.")
        return redirect("projects:pde_detail", project_id=project.id)

    row = ProjectDefinitionField.objects.filter(project=project, field_key=spec_key).first()
    status = (getattr(row, "status", "") or "")
    can_commit = is_project_committer(project, request.user)
    if status == ProjectDefinitionField.Status.PASS_LOCKED or (
        status == ProjectDefinitionField.Status.PROPOSED and not can_commit
    ):
        messages.error(request, "You do not have permission to open a topic chat for this section.")
        return redirect(reverse("projects:pde_detail", kwargs={"project_id": project.id}) + "#pde-field-" + spec_key)

    label = (getattr(spec, "label", "") or spec_key).strip()
    summary = (getattr(spec, "summary", "") or "").strip()
    help_text = (getattr(spec, "help_text", "") or "").strip()
    current_value = (getattr(row, "value_text", "") or "") if row else ""

    seed_user_text = "\n".join(
        [
            "Topic chat: PDE Field",
            "Scope: PDE",
            "Topic key: FIELD:" + spec_key,
            "Label: " + label,
            "Tier: " + (getattr(spec, "tier", "") or ""),
            "Required: " + ("yes" if getattr(spec, "required", True) else "no"),
            "",
            "Current value:",
            current_value or "(empty)",
            "",
            "Summary:",
            summary or "(none)",
            "",
            "Guidance:",
            help_text or "(none)",
            "",
            "Goal: Help produce a better draft for this field.",
            "Output: Replacement text only (no markdown).",
            "Success criteria: ready to paste into the field.",
        ]
    )

    chat = get_or_create_topic_chat(
        project=project,
        user=request.user,
        scope="PDE",
        topic_key="FIELD:" + spec_key,
        title="PDE-" + (project.name or "") + "-" + label + "-" + request.user.username,
        seed_user_text=seed_user_text,
        mode="CONTROLLED",
    )

    request.session["rw_active_project_id"] = project.id
    request.session["rw_active_chat_id"] = chat.id
    request.session.modified = True

    open_in = (request.POST.get("open_in") or "").strip().lower()
    if open_in == "drawer":
        base = reverse("projects:pde_detail", kwargs={"project_id": project.id})
        qs = "pde_chat_id=" + str(chat.id) + "&pde_chat_open=1"
        return redirect(base + "?" + qs + "#pde-field-" + spec_key)
    return redirect(reverse("accounts:chat_detail", args=[chat.id]))

@login_required
@require_http_methods(["GET", "POST"])
def pde_detail(request, project_id: int):
    project = get_object_or_404(Project, id=project_id)

    if not can_edit_pde(project, request.user):
        messages.error(request, "You do not have permission to edit this project definition.")
        return redirect("accounts:dashboard")
    can_commit = is_project_committer(project, request.user)

    def _generate_panes_for_user(*args, **kwargs):
        return generate_panes(*args, user=request.user, **kwargs)

    ensure_pde_fields(project)

    # If project is defined, ensure PDE is hydrated from the accepted CKO
    # whenever the PDE is not fully locked (eg. first reopen, or got disturbed).
    accepted = None
    skip_hydrate = bool(request.session.get("pde_skip_hydrate"))
    if project.defined_cko_id:
        accepted = ProjectCKO.objects.filter(id=project.defined_cko_id, project=project).first()

    pde_keys = {spec.key for spec in PDE_REQUIRED_FIELDS}
    rows_now = list(ProjectDefinitionField.objects.filter(project=project))
    has_nonlocked_pde = any(
        (row.field_key in pde_keys) and (row.status != ProjectDefinitionField.Status.PASS_LOCKED)
        for row in rows_now
    )

    if project.defined_cko_id and not skip_hydrate and not has_nonlocked_pde:
        state = _compute_pde_state(project)
        if not state.get("all_locked"):
            if accepted and isinstance(accepted.field_snapshot, dict) and accepted.field_snapshot:
                updated = 0
                for key, value in accepted.field_snapshot.items():
                    row = ProjectDefinitionField.objects.filter(project=project, field_key=key).first()
                    if not row:
                        continue
                    row.value_text = (value or "").strip()
                    row.status = ProjectDefinitionField.Status.PASS_LOCKED
                    row.last_validation = {}
                    row.save(update_fields=["value_text", "status", "last_validation", "updated_at"])
                    updated += 1

                pass
    elif skip_hydrate:
        request.session.pop("pde_skip_hydrate", None)
        request.session.modified = True

    action = (request.POST.get("action") or "").strip().lower()

    # Only show validation suggestions after an explicit validate action.
    if request.method == "POST" and action != "validate_lock":
        if request.session.get("pde_last_validation_key"):
            request.session.pop("pde_last_validation_key", None)
            request.session.modified = True

    seed_text = (project.purpose or "").strip()
    if request.method == "POST" and action in ("draft",):
        seed_text = (request.POST.get("seed_text") or "").strip() or seed_text

    # ------------------------------------------------------------
    # Action: Save and Exit (no validation)
    # ------------------------------------------------------------
    if request.method == "POST" and action == "save_exit":      
        request.session.pop("pde_help_auto_open_" + str(project.id), None)
        request.session.modified = True
        changed_any = False

        for spec in PDE_REQUIRED_FIELDS:
            row = ProjectDefinitionField.objects.get(project=project, field_key=spec.key)
            prior = (row.value_text or "").strip()
            proposed = (request.POST.get(spec.key) or "").strip()

            if row.status in (ProjectDefinitionField.Status.PROPOSED, ProjectDefinitionField.Status.PASS_LOCKED) and not can_commit:
                continue

            changed = (proposed != prior)
            if not changed:
                continue

            changed_any = True
            row.value_text = proposed

            if can_commit and row.status in (ProjectDefinitionField.Status.PROPOSED, ProjectDefinitionField.Status.PASS_LOCKED):
                row.status = ProjectDefinitionField.Status.DRAFT
                row.proposed_by = None
                row.proposed_at = None
                row.locked_by = None
                row.locked_at = None
            else:
                row.status = ProjectDefinitionField.Status.PROPOSED

            row.last_edited_by = request.user
            row.last_edited_at = timezone.now()

            row.save(
                update_fields=[
                    "value_text",
                    "status",
                    "last_validation",
                    "proposed_by",
                    "proposed_at",
                    "locked_by",
                    "locked_at",
                    "last_edited_by",
                    "last_edited_at",
                    "updated_at",
                ]
            )

        if changed_any:
            messages.success(request, "Changes saved.")
        else:
            messages.info(request, "No changes.")

        return redirect("accounts:project_config_info", project_id=project.id)

    # ------------------------------------------------------------
    # Action: Propose Lock
    # ------------------------------------------------------------
    if request.method == "POST" and action == "propose_lock":
        field_key = (request.POST.get("field_key") or "").strip()
        if not field_key:
            messages.error(request, "Missing field key.")
            return redirect(reverse("projects:pde_detail", kwargs={"project_id": project.id}) + "#pde-field-" + field_key)

        row = ProjectDefinitionField.objects.get(project=project, field_key=field_key)
        proposed_text = (request.POST.get("value_text") or "").strip()
        if not proposed_text:
            messages.error(request, "No value captured for proposal. Please try again.")
            return redirect(reverse("projects:pde_detail", kwargs={"project_id": project.id}) + "#pde-field-" + field_key)
        if proposed_text != (row.value_text or "").strip():
            row.value_text = proposed_text
            row.last_edited_by = request.user
            row.last_edited_at = timezone.now()
        locked_fields = read_locked_fields(project)
        spec = next((s for s in PDE_REQUIRED_FIELDS if s.key == field_key), None)
        rubric_text = getattr(spec, "help_text", "") if spec else ""
        vobj = validate_field(
            generate_panes_func=_generate_panes_for_user,
            field_key=field_key,
            value_text=proposed_text,
            locked_fields=locked_fields,
            rubric=rubric_text,
        )
        row.last_validation = vobj

        if vobj.get("verdict") != "PASS":
            row.status = ProjectDefinitionField.Status.DRAFT
            row.save(
                update_fields=[
                    "value_text",
                    "last_edited_by",
                    "last_edited_at",
                    "status",
                    "last_validation",
                    "updated_at",
                ]
            )
            messages.error(
                request,
                "Blocked at: " + field_key + " (" + str(vobj.get("verdict") or "") + ")",
            )
            request.session["pde_last_validation_key"] = field_key
            request.session["pde_skip_hydrate"] = True
            request.session.modified = True
            return redirect("projects:pde_detail", project_id=project.id)

        if can_commit:
            row.status = ProjectDefinitionField.Status.PASS_LOCKED
            row.locked_by = request.user
            row.locked_at = timezone.now()
            row.save(
                update_fields=[
                    "value_text",
                    "last_edited_by",
                    "last_edited_at",
                    "status",
                    "last_validation",
                    "locked_by",
                    "locked_at",
                    "updated_at",
                ]
            )
            messages.success(request, "Field locked.")
        else:
            row.status = ProjectDefinitionField.Status.PROPOSED
            row.proposed_by = request.user
            row.proposed_at = timezone.now()
            row.save(
                update_fields=[
                    "value_text",
                    "last_edited_by",
                    "last_edited_at",
                    "status",
                    "proposed_by",
                    "proposed_at",
                    "last_validation",
                    "updated_at",
                ]
            )
            messages.success(request, "Lock proposed: " + field_key)

        request.session["pde_skip_hydrate"] = True
        request.session.modified = True
        return redirect(reverse("projects:pde_detail", kwargs={"project_id": project.id}) + "#pde-field-" + field_key)

    # ------------------------------------------------------------
    # Action: Approve Lock
    # ------------------------------------------------------------
    if request.method == "POST" and action == "approve_lock":
        if not can_commit:
            messages.error(request, "Only the Project Committer can commit this project.")
            return redirect("projects:pde_detail", project_id=project.id)

        field_key = (request.POST.get("field_key") or "").strip()
        if not field_key:
            messages.error(request, "Missing field key.")
            return redirect("projects:pde_detail", project_id=project.id)

        row = ProjectDefinitionField.objects.get(project=project, field_key=field_key)
        proposed_text = (request.POST.get("value_text") or "").strip()
        if proposed_text and proposed_text != (row.value_text or "").strip():
            row.value_text = proposed_text
            row.last_edited_by = request.user
            row.last_edited_at = timezone.now()
        if row.status != ProjectDefinitionField.Status.PROPOSED:
            messages.error(request, "Field is not proposed.")
            return redirect(reverse("projects:pde_detail", kwargs={"project_id": project.id}) + "#pde-field-" + field_key)

        if (row.last_validation or {}).get("verdict") != "PASS":
            locked_fields = read_locked_fields(project)
            spec = next((s for s in PDE_REQUIRED_FIELDS if s.key == field_key), None)
            rubric_text = getattr(spec, "help_text", "") if spec else ""
            vobj = validate_field(
                generate_panes_func=_generate_panes_for_user,
                field_key=field_key,
                value_text=(row.value_text or "").strip(),
                locked_fields=locked_fields,
                rubric=rubric_text,
            )
            row.last_validation = vobj
            if vobj.get("verdict") != "PASS":
                row.save(update_fields=["last_validation", "updated_at"])
                messages.error(
                    request,
                    "Blocked at: " + field_key + " (" + str(vobj.get("verdict") or "") + ")",
                )
                request.session["pde_last_validation_key"] = field_key
                request.session["pde_skip_hydrate"] = True
                request.session.modified = True
                return redirect(reverse("projects:pde_detail", kwargs={"project_id": project.id}) + "#pde-field-" + field_key)

        row.status = ProjectDefinitionField.Status.PASS_LOCKED
        row.locked_by = request.user
        row.locked_at = timezone.now()
        row.save(
            update_fields=[
                "value_text",
                "last_edited_by",
                "last_edited_at",
                "status",
                "last_validation",
                "locked_by",
                "locked_at",
                "updated_at",
            ]
        )
        request.session["pde_skip_hydrate"] = True
        request.session.modified = True
        messages.success(request, "Field locked.")
        return redirect(reverse("projects:pde_detail", kwargs={"project_id": project.id}) + "#pde-field-" + field_key)

    # ------------------------------------------------------------
    # Action: Reopen Field
    # ------------------------------------------------------------
    if request.method == "POST" and action == "reopen_field":
        if not can_commit:
            messages.error(request, "Only the Project Committer can commit this project.")
            return redirect("projects:pde_detail", project_id=project.id)

        field_key = (request.POST.get("field_key") or "").strip()
        if not field_key:
            messages.error(request, "Missing field key.")
            return redirect("projects:pde_detail", project_id=project.id)

        row = ProjectDefinitionField.objects.get(project=project, field_key=field_key)
        row.status = ProjectDefinitionField.Status.DRAFT
        row.proposed_by = None
        row.proposed_at = None
        row.locked_by = None
        row.locked_at = None
        row.save(
            update_fields=[
                "status",
                "proposed_by",
                "proposed_at",
                "locked_by",
                "locked_at",
                "updated_at",
            ]
        )
        request.session["pde_skip_hydrate"] = True
        request.session.modified = True
        messages.success(request, "Field reopened: " + field_key)
        return redirect(reverse("projects:pde_detail", kwargs={"project_id": project.id}) + "#pde-field-" + field_key)

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
            a = _pde_help_answer(question=q, project=project, generate_panes_func=_generate_panes_for_user)
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
        res = draft_pde_from_seed(generate_panes_func=_generate_panes_for_user, seed_text=seed_text)
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

            pass

        return redirect("projects:pde_detail", project_id=project.id)

    # ------------------------------------------------------------
    # Action: Validate + lock (controlled loop)
    # ------------------------------------------------------------
    if request.method == "POST" and action == "validate_lock":
        if not can_commit:
            messages.error(request, "Only the Project Committer can commit this project.")
            return redirect("projects:pde_detail", project_id=project.id)
        user_inputs: Dict[str, str] = {}
        spec_map = {s.key: s for s in PDE_REQUIRED_FIELDS}
        for spec in PDE_REQUIRED_FIELDS:
            proposed = (request.POST.get(spec.key) or "").strip()
            user_inputs[spec.key] = proposed
            row = ProjectDefinitionField.objects.get(project=project, field_key=spec.key)
            prior = (row.value_text or "").strip()
            if proposed != prior:
                row.value_text = proposed
                row.status = ProjectDefinitionField.Status.PROPOSED
                row.proposed_by = request.user
                row.proposed_at = timezone.now()
                row.locked_by = None
                row.locked_at = None
                row.last_edited_by = request.user
                row.last_edited_at = timezone.now()
                row.save(
                    update_fields=[
                        "value_text",
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
        request.session["pde_skip_hydrate"] = True
        request.session.modified = True

        locked_fields = read_locked_fields(project)
        proposed_rows = list(
            ProjectDefinitionField.objects.filter(
                project=project,
                status=ProjectDefinitionField.Status.PROPOSED,
            )
        )

        if not proposed_rows:
            messages.info(request, "No proposed fields to validate.")
            return redirect("projects:pde_detail", project_id=project.id)

        first_blocker = None
        for row in proposed_rows:
            spec = spec_map.get(row.field_key)
            rubric_text = getattr(spec, "help_text", "") if spec else ""
            vobj = validate_field(
                generate_panes_func=_generate_panes_for_user,
                field_key=row.field_key,
                value_text=(row.value_text or "").strip(),
                locked_fields=locked_fields,
                rubric=rubric_text,
            )

            if vobj.get("verdict") != "PASS":
                row.last_validation = vobj
                row.save(update_fields=["last_validation", "updated_at"])
                first_blocker = vobj
                continue

            locked_value = (vobj.get("suggested_revision") or row.value_text or "").strip()
            locked_fields[row.field_key] = locked_value
            row.status = ProjectDefinitionField.Status.PASS_LOCKED
            row.value_text = locked_value
            row.last_validation = vobj
            row.locked_at = timezone.now()
            row.locked_by = request.user
            row.save(
                update_fields=[
                    "status",
                    "value_text",
                    "last_validation",
                    "locked_at",
                    "locked_by",
                    "updated_at",
                ]
            )

        if first_blocker:
            messages.error(
                request,
                "Blocked at: " + str(first_blocker.get("field_key") or "") + " (" + str(first_blocker.get("verdict") or "") + ")",
            )
            request.session["pde_last_validation_key"] = str(first_blocker.get("field_key") or "")
            request.session.modified = True
        else:
            messages.success(request, "Proposed fields locked.")
            request.session.pop("pde_last_validation_key", None)
            request.session.modified = True

        return redirect("projects:pde_detail", project_id=project.id)

    # ------------------------------------------------------------
    # Action: Commit (create DRAFT CKO) -> Preview
    # ------------------------------------------------------------

    pde_has_changes = True
    # NOTE: Ignore non-PDE fields (not in PDE_REQUIRED_FIELDS) for PDE state.
    all_rows_locked = True
    for row in rows_now:
        if row.field_key not in pde_keys:
            continue
        if row.status != ProjectDefinitionField.Status.PASS_LOCKED:
            all_rows_locked = False
            break

    if project.defined_cko_id and accepted and isinstance(accepted.field_snapshot, dict):
        if all_rows_locked:
            pde_has_changes = False
            snap = accepted.field_snapshot or {}
            if snap:
                row_map = {(row.field_key or "").strip(): row for row in rows_now if row.field_key in pde_keys}
                for key, snap_val in snap.items():
                    if key not in pde_keys:
                        continue
                    row = row_map.get((key or "").strip())
                    if not row:
                        continue
                    if (row.value_text or "").strip() != (snap_val or "").strip():
                        pde_has_changes = True
                        break
        else:
            pde_has_changes = True

    if request.method == "POST" and action == "commit":
        if not can_commit:
            messages.error(request, "Only the Project Committer can commit this project.")
            return redirect("projects:pde_detail", project_id=project.id)
        if project.defined_cko_id and not pde_has_changes:
            messages.info(request, "No changes to commit.")
            return redirect("projects:pde_detail", project_id=project.id)
        state = _compute_pde_state(project)
        if not state.get("all_locked"):
            messages.error(request, "Cannot commit: not all required fields are locked.")
            return redirect("projects:pde_detail", project_id=project.id)

        # Create DRAFT ProjectCKO snapshot (no acceptance here).
        try:
            res = commit_project_definition(
                project=project,
                required_keys=pde_required_keys_for_defined(),
            )
        except Exception as e:
            messages.error(request, "Commit failed: " + str(e))
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
                "proposed_by": (getattr(getattr(row, "proposed_by", None), "username", "") or "") if row else "",
                "locked_by": (getattr(getattr(row, "locked_by", None), "username", "") or "") if row else "",
                "summary": getattr(spec, "summary", ""),
                "help_text": getattr(spec, "help_text", ""),

            }
        )

    pde_chat_id_raw = (request.GET.get("pde_chat_id") or "").strip()
    if not pde_chat_id_raw:
        pde_chat_id_raw = str(request.session.get("pde_drawer_chat_id") or "")
    open_param = (request.GET.get("pde_chat_open") or "").strip()
    if open_param in ("0", "1"):
        request.session["pde_drawer_open"] = (open_param == "1")
        request.session.modified = True

    selected_chat_id = int(pde_chat_id_raw) if pde_chat_id_raw.isdigit() else None
    if selected_chat_id is not None:
        request.session["pde_drawer_chat_id"] = selected_chat_id
        request.session.modified = True

    topic_keys = ["FIELD:" + s["key"] for s in specs]
    bindings = (
        ProjectTopicChat.objects
        .select_related("chat")
        .filter(project=project, user=request.user, scope="PDE", topic_key__in=topic_keys)
    )
    topic_key_to_chat = {b.topic_key: b.chat for b in bindings if b.chat_id}

    chat_ctx_map = {}
    for topic_key, chat in topic_key_to_chat.items():
        ctx = build_chat_turn_context(request, chat)
        qs = request.GET.copy()
        qs["pde_chat_id"] = str(chat.id)
        qs["pde_chat_open"] = "1"
        qs.pop("turn", None)
        qs.pop("system", None)
        ctx["chat"] = chat
        ctx["qs_base"] = qs.urlencode()
        if selected_chat_id == chat.id:
            if open_param in ("0", "1"):
                ctx["is_open"] = (open_param == "1")
            else:
                ctx["is_open"] = bool(request.session.get("pde_drawer_open"))
        else:
            ctx["is_open"] = False
        chat_ctx_map[chat.id] = ctx

    for s in specs:
        topic_key = "FIELD:" + s["key"]
        chat = topic_key_to_chat.get(topic_key)
        s["topic_chat_id"] = chat.id if chat else None
        s["topic_chat_ctx"] = chat_ctx_map.get(chat.id) if chat else None
    current_field_key = ""
    for s in specs:
        if (s.get("status") or "") != ProjectDefinitionField.Status.PASS_LOCKED:
            current_field_key = s.get("key") or ""
            break


    state = _compute_pde_state(project)
    ui_all_locked = all_rows_locked
    ui_has_proposed = any(
        r.status == ProjectDefinitionField.Status.PROPOSED and r.field_key in pde_keys
        for r in rows_now
    )
    if ui_has_proposed:
        state["badge"] = {"text": "Proposed", "class": "bg-warning text-dark"}
    elif project.defined_cko_id and not pde_has_changes:
        state["badge"] = {"text": "Committed", "class": "bg-success"}
    elif ui_all_locked:
        state["badge"] = {"text": "Ready to Commit", "class": "bg-success"}
    pde_help_log = _get_help_log(request, project.id)
    auto_key = "pde_help_auto_open_" + str(project.id)
    pde_help_auto_open = bool(request.session.get(auto_key))
    if pde_help_auto_open:
        request.session.pop(auto_key, None)
        request.session.modified = True

    show_validation_key = (request.session.get("pde_last_validation_key") or "").strip()
    if show_validation_key:
        request.session.pop("pde_last_validation_key", None)
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
            "all_locked": ui_all_locked,
            "pde_can_commit": can_commit,
            "pde_has_changes": pde_has_changes,
            "show_validation_key": show_validation_key,
            "pde_help_log": pde_help_log,
            "pde_help_auto_open": pde_help_auto_open,
            "ui_return_to": reverse("accounts:project_home", kwargs={"project_id": project.id}),
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

