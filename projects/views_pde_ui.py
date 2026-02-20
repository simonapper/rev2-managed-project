# -*- coding: utf-8 -*-
# projects/views_pde_ui.py
#
# PDE v1 - Minimal UI: seed -> draft -> validate/lock -> commit.
# NOTE: Keep code comments 7-bit ASCII only.

from __future__ import annotations

from typing import Any, Dict, List

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_http_methods, require_POST
from django.urls import reverse
from django.utils import timezone

from chats.services.llm import generate_panes
from projects.models import Project, ProjectDefinitionField, ProjectCKO, ProjectTopicChat, UserProjectPrefs
from projects.services.pde import draft_pde_from_seed, validate_field
from projects.services.pde_commit import commit_project_definition
from projects.services.pde_required_keys import pde_required_keys_for_defined
from projects.services.pde_loop import ensure_pde_fields, read_locked_fields
from projects.services.pde_spec import PDE_REQUIRED_FIELDS
from projects.services_topic_chat import get_or_create_topic_chat
from projects.services_project_membership import can_edit_pde, is_project_committer
from chats.services.turns import build_chat_turn_context


ALLOWED_SEED_STYLES = {"concise", "balanced", "detailed"}
ALLOWED_PROJECT_TYPES = {"META", "KNOWLEDGE", "DELIVERY", "RESEARCH", "OPERATIONS"}
ALLOWED_PROJECT_STATUS = {"ACTIVE", "PAUSED", "ARCHIVED"}


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


def _validate_direct_lock_field(field_key: str, value_text: str) -> Dict[str, Any] | None:
    if field_key not in {"identity.project_type", "identity.project_status", "storage.artefact_root_ref"}:
        return None
    raw = str(value_text or "").strip()
    val = raw.upper()
    if field_key == "identity.project_type":
        allowed = ALLOWED_PROJECT_TYPES
        label = "Project type"
    elif field_key == "identity.project_status":
        allowed = ALLOWED_PROJECT_STATUS
        label = "Project status"
    else:
        # Artefact root is either SYSTEM or a user path.
        if not raw:
            return {
                "field_key": field_key,
                "verdict": "PASS",
                "issues": [],
                "suggested_revision": "SYSTEM",
                "questions": [],
                "confidence": "HIGH",
            }
        if val == "SYSTEM":
            return {
                "field_key": field_key,
                "verdict": "PASS",
                "issues": [],
                "suggested_revision": "SYSTEM",
                "questions": [],
                "confidence": "HIGH",
            }
        looks_like_path = any(ch in raw for ch in ("/", "\\", ":"))
        if not looks_like_path:
            return {
                "field_key": field_key,
                "verdict": "WEAK",
                "issues": ["Use SYSTEM or a folder path."],
                "suggested_revision": "",
                "questions": [],
                "confidence": "HIGH",
            }
        return {
            "field_key": field_key,
            "verdict": "PASS",
            "issues": [],
            "suggested_revision": raw,
            "questions": [],
            "confidence": "HIGH",
        }
    if not val:
        return {
            "field_key": field_key,
            "verdict": "WEAK",
            "issues": [label + " is required."],
            "suggested_revision": "",
            "questions": [],
            "confidence": "HIGH",
        }
    if val not in allowed:
        opts = ", ".join(sorted(allowed))
        return {
            "field_key": field_key,
            "verdict": "WEAK",
            "issues": [label + " must be one of: " + opts + "."],
            "suggested_revision": "",
            "questions": [],
            "confidence": "HIGH",
        }
    return {
        "field_key": field_key,
        "verdict": "PASS",
        "issues": [],
        "suggested_revision": val,
        "questions": [],
        "confidence": "HIGH",
    }


def _get_user_project_seed_defaults(project: Project, user) -> tuple[str, str]:
    prefs = UserProjectPrefs.objects.filter(project=project, user=user).first()
    ui = prefs.ui_overrides if prefs and isinstance(prefs.ui_overrides, dict) else {}
    return (
        _normalise_seed_style(ui.get("rw_seed_style")),
        _normalise_seed_constraints(ui.get("rw_seed_constraints")),
    )


def _fields_for_project(project: Project) -> Dict[str, ProjectDefinitionField]:
    qs = ProjectDefinitionField.objects.filter(project=project)
    out: Dict[str, ProjectDefinitionField] = {}
    for row in qs:
        k = (row.field_key or "").strip()
        if k:
            out[k] = row
    return out


def _normalise_pde_field_metadata(project: Project) -> None:
    rows = ProjectDefinitionField.objects.filter(project=project)
    for row in rows:
        changed = False
        update_fields: List[str] = []
        if row.status == ProjectDefinitionField.Status.PASS_LOCKED:
            if row.proposed_by_id is not None:
                row.proposed_by = None
                changed = True
                update_fields.append("proposed_by")
            if row.proposed_at is not None:
                row.proposed_at = None
                changed = True
                update_fields.append("proposed_at")
        else:
            if row.locked_by_id is not None:
                row.locked_by = None
                changed = True
                update_fields.append("locked_by")
            if row.locked_at is not None:
                row.locked_at = None
                changed = True
                update_fields.append("locked_at")
            if row.status != ProjectDefinitionField.Status.PROPOSED:
                if row.proposed_by_id is not None:
                    row.proposed_by = None
                    changed = True
                    update_fields.append("proposed_by")
                if row.proposed_at is not None:
                    row.proposed_at = None
                    changed = True
                    update_fields.append("proposed_at")
        if changed:
            row.save(update_fields=list(dict.fromkeys(update_fields + ["updated_at"])))


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


def _build_blocked_validation_feedback(field_key: str, vobj: Dict[str, Any]) -> tuple[str, List[str], str]:
    verdict = str(vobj.get("verdict") or "").strip() or "WEAK"
    issues_raw = vobj.get("issues")
    issues: List[str] = []
    if isinstance(issues_raw, list):
        issues = [str(x).strip() for x in issues_raw if str(x).strip()]
    suggested = str(vobj.get("suggested_revision") or "").strip()
    base = "Blocked at: " + (field_key or "") + " (" + verdict + ")"
    if issues:
        base += " - " + issues[0]
    return base, issues, suggested


@require_POST
@login_required
def pde_topic_chat_open(request, project_id: int):
    wants_json = (request.headers.get("X-Requested-With") or "").lower() == "xmlhttprequest"
    project = get_object_or_404(Project, pk=project_id)
    if not can_edit_pde(project, request.user):
        if wants_json:
            return JsonResponse({"ok": False, "message": "Permission denied."}, status=403)
        messages.error(request, "You do not have permission to open a topic chat for this section.")
        return redirect("projects:pde_detail", project_id=project.id)

    spec_key = (request.POST.get("spec_key") or "").strip()
    if not spec_key:
        if wants_json:
            return JsonResponse({"ok": False, "message": "Invalid field."}, status=400)
        messages.error(request, "Invalid field.")
        return redirect("projects:pde_detail", project_id=project.id)

    spec_map = {s.key: s for s in PDE_REQUIRED_FIELDS}
    spec = spec_map.get(spec_key)
    if not spec:
        if wants_json:
            return JsonResponse({"ok": False, "message": "Field not found."}, status=404)
        messages.error(request, "Field not found.")
        return redirect("projects:pde_detail", project_id=project.id)

    row = ProjectDefinitionField.objects.filter(project=project, field_key=spec_key).first()
    status = (getattr(row, "status", "") or "")
    can_commit = is_project_committer(project, request.user)
    if status == ProjectDefinitionField.Status.PASS_LOCKED or (
        status == ProjectDefinitionField.Status.PROPOSED and not can_commit
    ):
        if wants_json:
            return JsonResponse({"ok": False, "message": "Permission denied for this field."}, status=403)
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
    if spec_key == "canonical.summary":
        seed_user_text += "\n\n" + "\n".join(
            [
                "Canonical summary instruction:",
                "- Write exactly one line.",
                "- Keep it between 10 and 15 words.",
                "- Summarise the seed intent only.",
                "- Do not include bullets, labels, or markdown.",
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

    if wants_json:
        ctx = build_chat_turn_context(request, chat)
        qs = request.GET.copy()
        qs["pde_chat_id"] = str(chat.id)
        qs["pde_chat_open"] = "1"
        qs.pop("turn", None)
        qs.pop("system", None)
        ctx["chat"] = chat
        ctx["qs_base"] = qs.urlencode()
        ctx["is_open"] = True
        ctx["apply_target"] = "pde_field:" + spec_key
        drawer_html = render_to_string(
            "projects/ppde_inline_chat.html",
            {"chat_ctx": ctx},
            request=request,
        )
        return JsonResponse({"ok": True, "chat_id": chat.id, "drawer_html": drawer_html})

    open_in = (request.POST.get("open_in") or "").strip().lower()
    if open_in == "drawer":
        base = reverse("projects:pde_detail", kwargs={"project_id": project.id})
        qs = "pde_chat_id=" + str(chat.id) + "&pde_chat_open=1"
        return redirect(base + "?" + qs)
    return redirect(reverse("accounts:chat_detail", args=[chat.id]))

@login_required
@require_http_methods(["GET", "POST"])
def pde_detail(request, project_id: int):
    project = get_object_or_404(Project, id=project_id)
    wants_json = (request.headers.get("X-Requested-With") or "").lower() == "xmlhttprequest"

    if not can_edit_pde(project, request.user):
        messages.error(request, "You do not have permission to edit this project definition.")
        return redirect("accounts:dashboard")
    can_commit = is_project_committer(project, request.user)
    pref_seed_style, pref_seed_constraints = _get_user_project_seed_defaults(project, request.user)
    seed_style = _normalise_seed_style(request.session.get("rw_seed_style") or pref_seed_style)
    seed_constraints = _normalise_seed_constraints(
        request.session.get("rw_seed_constraints") or pref_seed_constraints
    )

    if request.method == "GET" and (request.GET.get("apply_seed_controls") or "").strip() == "1":
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

    def _generate_panes_for_user(*args, **kwargs):
        return generate_panes(*args, user=request.user, **kwargs)

    ensure_pde_fields(project)
    _normalise_pde_field_metadata(project)

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
    if request.method == "POST" and action in ("draft", "apply_seed_controls"):
        seed_style = _normalise_seed_style(request.POST.get("seed_style") or seed_style)
        seed_constraints = _normalise_seed_constraints(request.POST.get("seed_constraints") or seed_constraints)
        request.session["rw_seed_style"] = seed_style
        request.session["rw_seed_constraints"] = seed_constraints
        request.session.modified = True
        if action == "apply_seed_controls":
            if (request.POST.get("make_default") or "").strip() == "1":
                prefs, _ = UserProjectPrefs.objects.get_or_create(project=project, user=request.user)
                ui = prefs.ui_overrides if isinstance(prefs.ui_overrides, dict) else {}
                ui["rw_seed_style"] = seed_style
                ui["rw_seed_constraints"] = seed_constraints
                prefs.ui_overrides = ui
                prefs.save(update_fields=["ui_overrides", "updated_at"])
                messages.success(request, "Seed style applied and set as default for this project.")
            else:
                messages.success(request, "Seed style applied.")
            return redirect("projects:pde_detail", project_id=project.id)
    draft_raw_output = ""

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
            if wants_json:
                return JsonResponse({"ok": False, "message": "Missing field key."}, status=400)
            messages.error(request, "Missing field key.")
            return redirect(reverse("projects:pde_detail", kwargs={"project_id": project.id}) + "#pde-field-" + field_key)

        row = ProjectDefinitionField.objects.get(project=project, field_key=field_key)
        proposed_text = (request.POST.get("value_text") or "").strip()
        if not proposed_text:
            if wants_json:
                return JsonResponse({"ok": False, "message": "No value captured for proposal."}, status=400)
            messages.error(request, "No value captured for proposal. Please try again.")
            return redirect(reverse("projects:pde_detail", kwargs={"project_id": project.id}) + "#pde-field-" + field_key)
        if proposed_text != (row.value_text or "").strip():
            row.value_text = proposed_text
            row.last_edited_by = request.user
            row.last_edited_at = timezone.now()
        locked_fields = read_locked_fields(project)
        spec = next((s for s in PDE_REQUIRED_FIELDS if s.key == field_key), None)
        rubric_text = getattr(spec, "help_text", "") if spec else ""
        vobj = _validate_direct_lock_field(field_key, proposed_text)
        if vobj is None:
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
            row.proposed_by = None
            row.proposed_at = None
            row.locked_by = None
            row.locked_at = None
            row.save(
                update_fields=[
                    "value_text",
                    "last_edited_by",
                    "last_edited_at",
                    "status",
                    "last_validation",
                    "proposed_by",
                    "proposed_at",
                    "locked_by",
                    "locked_at",
                    "updated_at",
                ]
            )
            blocked_msg, blocked_issues, blocked_suggested = _build_blocked_validation_feedback(field_key, vobj)
            messages.error(request, blocked_msg)
            for issue in blocked_issues[1:3]:
                messages.error(request, "Issue: " + issue)
            if blocked_suggested:
                messages.info(request, "Suggested revision: " + blocked_suggested)
            request.session["pde_last_validation_key"] = field_key
            request.session["pde_skip_hydrate"] = True
            request.session.modified = True
            if wants_json:
                return JsonResponse(
                    {
                        "ok": False,
                        "message": blocked_msg,
                        "field_key": field_key,
                        "issues": blocked_issues,
                        "suggested_revision": blocked_suggested,
                    },
                    status=400,
                )
            return redirect("projects:pde_detail", project_id=project.id)

        locked_value = str(vobj.get("suggested_revision") or proposed_text or "").strip()
        if field_key == "storage.artefact_root_ref" and not locked_value:
            locked_value = "SYSTEM"
        if locked_value != (row.value_text or "").strip():
            row.value_text = locked_value
            row.last_edited_by = request.user
            row.last_edited_at = timezone.now()

        if can_commit:
            row.status = ProjectDefinitionField.Status.PASS_LOCKED
            row.proposed_by = None
            row.proposed_at = None
            row.locked_by = request.user
            row.locked_at = timezone.now()
            row.save(
                update_fields=[
                    "value_text",
                    "last_edited_by",
                    "last_edited_at",
                    "status",
                    "last_validation",
                    "proposed_by",
                    "proposed_at",
                    "locked_by",
                    "locked_at",
                    "updated_at",
                ]
            )
            messages.success(request, "Field locked.")
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "field_key": field_key,
                        "status": "PASS_LOCKED",
                        "locked_by": request.user.username,
                    }
                )
        else:
            row.status = ProjectDefinitionField.Status.PROPOSED
            row.proposed_by = request.user
            row.proposed_at = timezone.now()
            row.locked_by = None
            row.locked_at = None
            row.save(
                update_fields=[
                    "value_text",
                    "last_edited_by",
                    "last_edited_at",
                    "status",
                    "proposed_by",
                    "proposed_at",
                    "locked_by",
                    "locked_at",
                    "last_validation",
                    "updated_at",
                ]
            )
            messages.success(request, "Lock proposed: " + field_key)
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "field_key": field_key,
                        "status": "PROPOSED",
                        "proposed_by": request.user.username,
                    }
                )

        request.session["pde_skip_hydrate"] = True
        request.session.modified = True
        return redirect(reverse("projects:pde_detail", kwargs={"project_id": project.id}) + "#pde-field-" + field_key)

    # ------------------------------------------------------------
    # Action: Approve Lock
    # ------------------------------------------------------------
    if request.method == "POST" and action == "approve_lock":
        if not can_commit:
            if wants_json:
                return JsonResponse({"ok": False, "message": "Only the Project Committer can commit this project."}, status=403)
            messages.error(request, "Only the Project Committer can commit this project.")
            return redirect("projects:pde_detail", project_id=project.id)

        field_key = (request.POST.get("field_key") or "").strip()
        if not field_key:
            if wants_json:
                return JsonResponse({"ok": False, "message": "Missing field key."}, status=400)
            messages.error(request, "Missing field key.")
            return redirect("projects:pde_detail", project_id=project.id)

        row = ProjectDefinitionField.objects.get(project=project, field_key=field_key)
        proposed_text = (request.POST.get("value_text") or "").strip()
        value_changed = False
        if proposed_text and proposed_text != (row.value_text or "").strip():
            row.value_text = proposed_text
            row.last_edited_by = request.user
            row.last_edited_at = timezone.now()
            value_changed = True
        if value_changed:
            row.save(update_fields=["value_text", "last_edited_by", "last_edited_at", "updated_at"])
        if row.status != ProjectDefinitionField.Status.PROPOSED:
            if wants_json:
                return JsonResponse({"ok": False, "message": "Field is not proposed."}, status=400)
            messages.error(request, "Field is not proposed.")
            return redirect(reverse("projects:pde_detail", kwargs={"project_id": project.id}) + "#pde-field-" + field_key)

        if (row.last_validation or {}).get("verdict") != "PASS":
            locked_fields = read_locked_fields(project)
            spec = next((s for s in PDE_REQUIRED_FIELDS if s.key == field_key), None)
            rubric_text = getattr(spec, "help_text", "") if spec else ""
            vobj = _validate_direct_lock_field(field_key, (row.value_text or "").strip())
            if vobj is None:
                vobj = validate_field(
                    generate_panes_func=_generate_panes_for_user,
                    field_key=field_key,
                    value_text=(row.value_text or "").strip(),
                    locked_fields=locked_fields,
                    rubric=rubric_text,
                )
            row.last_validation = vobj
            if vobj.get("verdict") != "PASS":
                row.status = ProjectDefinitionField.Status.PROPOSED
                row.locked_by = None
                row.locked_at = None
                row.save(
                    update_fields=[
                        "value_text",
                        "last_edited_by",
                        "last_edited_at",
                        "status",
                        "locked_by",
                        "locked_at",
                        "last_validation",
                        "updated_at",
                    ]
                )
                blocked_msg, blocked_issues, blocked_suggested = _build_blocked_validation_feedback(field_key, vobj)
                messages.error(request, blocked_msg)
                for issue in blocked_issues[1:3]:
                    messages.error(request, "Issue: " + issue)
                if blocked_suggested:
                    messages.info(request, "Suggested revision: " + blocked_suggested)
                request.session["pde_last_validation_key"] = field_key
                request.session["pde_skip_hydrate"] = True
                request.session.modified = True
                if wants_json:
                    return JsonResponse(
                        {
                            "ok": False,
                            "message": blocked_msg,
                            "field_key": field_key,
                            "issues": blocked_issues,
                            "suggested_revision": blocked_suggested,
                        },
                        status=400,
                    )
                return redirect(reverse("projects:pde_detail", kwargs={"project_id": project.id}) + "#pde-field-" + field_key)

        locked_value = str((row.last_validation or {}).get("suggested_revision") or row.value_text or "").strip()
        if field_key == "storage.artefact_root_ref" and not locked_value:
            locked_value = "SYSTEM"
        row.value_text = locked_value
        row.status = ProjectDefinitionField.Status.PASS_LOCKED
        row.proposed_by = None
        row.proposed_at = None
        row.locked_by = request.user
        row.locked_at = timezone.now()
        row.save(
            update_fields=[
                "value_text",
                "last_edited_by",
                "last_edited_at",
                "status",
                "last_validation",
                "proposed_by",
                "proposed_at",
                "locked_by",
                "locked_at",
                "updated_at",
            ]
        )
        request.session["pde_skip_hydrate"] = True
        request.session.modified = True
        messages.success(request, "Field locked.")
        if wants_json:
            return JsonResponse(
                {
                    "ok": True,
                    "field_key": field_key,
                    "status": "PASS_LOCKED",
                    "locked_by": request.user.username,
                }
            )
        return redirect(reverse("projects:pde_detail", kwargs={"project_id": project.id}) + "#pde-field-" + field_key)

    # ------------------------------------------------------------
    # Action: Override Lock (bypasses LLM; committer only)
    # ------------------------------------------------------------
    if request.method == "POST" and action == "override_lock":
        if not can_commit:
            messages.error(request, "Only the Project Committer can override-lock a field.")
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
        row.status = ProjectDefinitionField.Status.PASS_LOCKED
        row.proposed_by = None
        row.proposed_at = None
        row.locked_by = request.user
        row.locked_at = timezone.now()
        row.last_validation = {"verdict": "PASS", "issues": [], "override": True}
        row.save(update_fields=[
            "value_text", "last_edited_by", "last_edited_at",
            "status", "last_validation",
            "proposed_by", "proposed_at",
            "locked_by", "locked_at", "updated_at",
        ])
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": True, "locked_by": request.user.username})
        request.session["pde_skip_hydrate"] = True
        request.session.modified = True
        messages.success(request, "Field override-locked.")
        return redirect(reverse("projects:pde_detail", kwargs={"project_id": project.id}) + "#pde-field-" + field_key)

    # ------------------------------------------------------------
    # Action: Reopen Field
    # ------------------------------------------------------------
    if request.method == "POST" and action == "reopen_field":
        if not can_commit:
            if wants_json:
                return JsonResponse({"ok": False, "message": "Only the Project Committer can commit this project."}, status=403)
            messages.error(request, "Only the Project Committer can commit this project.")
            return redirect("projects:pde_detail", project_id=project.id)

        field_key = (request.POST.get("field_key") or "").strip()
        if not field_key:
            if wants_json:
                return JsonResponse({"ok": False, "message": "Missing field key."}, status=400)
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
        if wants_json:
            return JsonResponse(
                {
                    "ok": True,
                    "field_key": field_key,
                    "status": "DRAFT",
                }
            )
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
        res = draft_pde_from_seed(
            generate_panes_func=_generate_panes_for_user,
            seed_text=seed_text,
            seed_style=seed_style,
            seed_constraints=seed_constraints,
        )
        if not res.get("ok"):
            raw_text = str(res.get("raw") or "").strip()
            draft_raw_output = raw_text or "[No model text returned in any pane.]"
            messages.error(
                request,
                "Draft failed: "
                + str(res.get("error") or "unknown error")
                + " Raw model output was loaded into Canonical summary.",
            )
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
                row.proposed_by = request.user
                row.proposed_at = timezone.now()
                row.locked_by = None
                row.locked_at = None
                row.last_validation = {}
                row.save(
                    update_fields=[
                        "value_text",
                        "status",
                        "proposed_by",
                        "proposed_at",
                        "locked_by",
                        "locked_at",
                        "last_validation",
                        "updated_at",
                    ]
                )
                updated += 1

            return redirect("projects:pde_detail", project_id=project.id)

    # ------------------------------------------------------------
    # Action: Validate + lock (controlled loop)
    # ------------------------------------------------------------
    if request.method == "POST" and action == "validate_lock":
        if not can_commit:
            if wants_json:
                return JsonResponse({"ok": False, "message": "Only the Project Committer can commit this project."}, status=403)
            messages.error(request, "Only the Project Committer can commit this project.")
            return redirect("projects:pde_detail", project_id=project.id)
        spec_map = {s.key: s for s in PDE_REQUIRED_FIELDS}
        ignored_locked_fields: List[str] = []
        for spec in PDE_REQUIRED_FIELDS:
            proposed = (request.POST.get(spec.key) or "").strip()
            row = ProjectDefinitionField.objects.get(project=project, field_key=spec.key)
            if row.status == ProjectDefinitionField.Status.PASS_LOCKED:
                prior_locked = (row.value_text or "").strip()
                if proposed != prior_locked:
                    ignored_locked_fields.append(spec.key)
                continue
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
            elif row.status == ProjectDefinitionField.Status.PROPOSED and (row.locked_by_id or row.locked_at):
                row.locked_by = None
                row.locked_at = None
                row.save(update_fields=["locked_by", "locked_at", "updated_at"])
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
            if wants_json:
                return JsonResponse({"ok": True, "message": "No proposed fields to validate.", "locked_keys": []})
            messages.info(request, "No proposed fields to validate.")
            return redirect("projects:pde_detail", project_id=project.id)

        first_blocker = None
        locked_keys: List[str] = []
        for row in proposed_rows:
            spec = spec_map.get(row.field_key)
            rubric_text = getattr(spec, "help_text", "") if spec else ""
            vobj = _validate_direct_lock_field(row.field_key, (row.value_text or "").strip())
            if vobj is None:
                vobj = validate_field(
                    generate_panes_func=_generate_panes_for_user,
                    field_key=row.field_key,
                    value_text=(row.value_text or "").strip(),
                    locked_fields=locked_fields,
                    rubric=rubric_text,
                )

            if vobj.get("verdict") != "PASS":
                vobj = dict(vobj or {})
                if not str(vobj.get("field_key") or "").strip():
                    vobj["field_key"] = row.field_key
                row.status = ProjectDefinitionField.Status.PROPOSED
                row.locked_by = None
                row.locked_at = None
                row.last_validation = vobj
                row.save(update_fields=["status", "locked_by", "locked_at", "last_validation", "updated_at"])
                first_blocker = vobj
                continue

            locked_value = (vobj.get("suggested_revision") or row.value_text or "").strip()
            locked_fields[row.field_key] = locked_value
            row.status = ProjectDefinitionField.Status.PASS_LOCKED
            row.value_text = locked_value
            row.last_validation = vobj
            row.proposed_by = None
            row.proposed_at = None
            row.locked_at = timezone.now()
            row.locked_by = request.user
            row.save(
                update_fields=[
                    "status",
                    "value_text",
                    "last_validation",
                    "proposed_by",
                    "proposed_at",
                    "locked_at",
                    "locked_by",
                    "updated_at",
                ]
            )
            locked_keys.append(row.field_key)

        if ignored_locked_fields:
            messages.info(
                request,
                "Ignored edits to locked fields: " + ", ".join(ignored_locked_fields) + ". Reopen to edit.",
            )
        if first_blocker:
            blocker_key = str(first_blocker.get("field_key") or "")
            blocked_msg, blocked_issues, blocked_suggested = _build_blocked_validation_feedback(blocker_key, first_blocker)
            messages.error(request, blocked_msg)
            for issue in blocked_issues[1:3]:
                messages.error(request, "Issue: " + issue)
            if blocked_suggested:
                messages.info(request, "Suggested revision: " + blocked_suggested)
            request.session["pde_last_validation_key"] = blocker_key
            request.session.modified = True
            if wants_json:
                return JsonResponse(
                    {
                        "ok": False,
                        "message": blocked_msg,
                        "field_key": blocker_key,
                        "issues": blocked_issues,
                        "suggested_revision": blocked_suggested,
                        "locked_keys": locked_keys,
                    },
                    status=400,
                )
        else:
            messages.success(request, "Proposed fields locked.")
            request.session.pop("pde_last_validation_key", None)
            request.session.modified = True
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "message": "Proposed fields locked.",
                        "locked_keys": locked_keys,
                        "locked_by": _display_name(request.user),
                    }
                )

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
        value_text = (getattr(row, "value_text", "") or "") if row else ""
        if spec.key == "storage.artefact_root_ref" and not value_text.strip():
            value_text = "SYSTEM"
        if draft_raw_output and spec.key == "canonical.summary":
            value_text = draft_raw_output
        specs.append(
            {
                "key": spec.key,
                "label": spec.label,
                "tier": getattr(spec, "tier", ""),
                "required": getattr(spec, "required", True),
                "status": (getattr(row, "status", "") or "") if row else "",
                "value_text": value_text,
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
        if s["topic_chat_ctx"]:
            s["topic_chat_ctx"]["apply_target"] = "pde_field:" + s["key"]
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
            "seed_style": seed_style,
            "seed_constraints": seed_constraints,
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

