# -*- coding: utf-8 -*-
# projects/views_pde.py
#
# Add: POST /projects/pde/create_project_cko/
# - Gated on 4 baseline fields being PASS_LOCKED
# - Generates a single combined canonical document
# - Attempts to store as a KnowledgeObject (CKO) if models are available
# - Always returns the generated document in JSON (so you can verify quickly)

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.shortcuts import render, get_object_or_404, redirect

from projects.models import Project
from projects.models import ProjectDefinitionField

# NOTE: this import is used only by verify
from projects.services.pde import validate_field
from chats.services.llm import generate_panes

from django.contrib import messages
from projects.services.pde_commit import commit_project_definition
from projects.services.pde_required_keys import pde_required_keys_for_defined


BASELINE_L1_MUST_KEYS = (
    "intent.primary_goal",
    "success.acceptance_test",
    "constraints.primary_constraint",
    "authority.decision_owner",
)


def _get_locked_fields_for_project(project_id: int) -> dict[str, str]:
    locked_qs = (
        ProjectDefinitionField.objects
        .filter(project_id=project_id, status="PASS_LOCKED")
        .only("field_key", "value_text")
    )
    out: dict[str, str] = {}
    for f in locked_qs:
        k = (f.field_key or "").strip()
        v = (f.value_text or "").strip()
        if k and v:
            out[k] = v
    return out


def _rubric_for_field(field_key: str) -> str | None:
    if field_key == "intent.primary_goal":
        return "- Intent: actor + outcome; avoid implementation details.\n"
    if field_key == "success.acceptance_test":
        return "- Success: falsifiable/observable; include a clear pass condition.\n"
    if field_key == "constraints.primary_constraint":
        return "- Constraint: hard limit, not a preference.\n"
    if field_key == "authority.decision_owner":
        return "- Authority: name role/person and decision scope.\n"
    return None


def _canonical_summary_10_words(text: str) -> str:
    """
    One sentence, max 10 words, plain language.
    This is a simple heuristic for v1.
    """
    words = [w for w in (text or "").strip().split() if w]
    if not words:
        return "Define and lock L1 fields into a project CKO."
    summary = " ".join(words[:10])
    # Ensure it ends like a sentence without unicode punctuation.
    if not summary.endswith("."):
        summary = summary + "."
    return summary


def _build_project_cko_document(project: Project, locked: dict[str, str]) -> str:
    """
    Produce the combined doc text. Keep it stable and ASCII.
    """
    goal = locked.get("intent.primary_goal", "")
    canonical_summary = _canonical_summary_10_words(goal)

    now = timezone.now()
    date_str = now.strftime("%Y-%m-%d")

    lines: list[str] = []
    lines.append("# ============================================================")
    lines.append("# PROJECT CKO - L1 BASELINE (PDE v1)")
    lines.append("# ============================================================")
    lines.append("# Project: " + (project.name or ""))
    lines.append("# Project ID: " + str(project.id))
    lines.append("# Owner: " + str(getattr(project.owner, "username", "") or ""))
    lines.append("# Date: " + date_str)
    lines.append("# Status: DRAFT")
    lines.append("# ------------------------------------------------------------")
    lines.append("# CANONICAL SUMMARY (<=10 words)")
    lines.append("# ------------------------------------------------------------")
    lines.append("# " + canonical_summary)
    lines.append("")
    lines.append("# ------------------------------------------------------------")
    lines.append("# L1 BASELINE FIELDS (PASS_LOCKED)")
    lines.append("# ------------------------------------------------------------")
    for key in BASELINE_L1_MUST_KEYS:
        lines.append("")
        lines.append("## " + key)
        lines.append((locked.get(key, "") or "").strip())
    lines.append("")
    lines.append("# ============================================================")
    lines.append("# END")
    lines.append("# ============================================================")
    return "\n".join(lines)


def _store_project_cko_if_possible(project: Project, user, doc_text: str, canonical_summary: str) -> int | None:
    """
    Best-effort storage hook.
    Returns knowledge_object_id if stored, else None.

    NOTE: You may need to adjust field names to match your objects.models.
    """
    try:
        from objects.models import KnowledgeObject, KnowledgeObjectVersion  # type: ignore
    except Exception:
        return None

    # Best guess: keep this conservative and easy to adapt.
    try:
        ko = KnowledgeObject.objects.create(
            project=project,
            title="Project CKO - L1 Baseline",
            kind="CKO",
            status="DRAFT",
            canonical_summary=canonical_summary,
            created_by=user,
        )
        KnowledgeObjectVersion.objects.create(
            knowledge_object=ko,
            version_number=1,
            content_text=doc_text,
            created_by=user,
        )
        return ko.id
    except Exception:
        # If your model fields differ, adapt this block and retry.
        return None


@login_required
@require_POST
def pde_field_verify(request):
    project_id_raw = (request.POST.get("project_id") or "").strip()
    field_key = (request.POST.get("field_key") or "").strip()
    value_text = (request.POST.get("value_text") or "").strip()

    if not project_id_raw or not field_key:
        return JsonResponse({"error": "Missing project_id or field_key."}, status=400)

    try:
        project_id = int(project_id_raw)
    except ValueError:
        return JsonResponse({"error": "Invalid project_id."}, status=400)

    project = Project.objects.filter(id=project_id, owner=request.user).first()
    if not project:
        return JsonResponse({"error": "Project not found."}, status=404)

    field_obj, _created = ProjectDefinitionField.objects.get_or_create(
        project_id=project_id,
        field_key=field_key,
        defaults={
            "tier": "L1-MUST",
            "status": "DRAFT",
            "value_text": "",
            "last_validation": {},
        },
    )

    field_obj.value_text = value_text
    field_obj.save(update_fields=["value_text", "updated_at"])

    locked_fields = _get_locked_fields_for_project(project_id)
    rubric = _rubric_for_field(field_key)

    result = validate_field(
        generate_panes_func=generate_panes,
        field_key=field_key,
        value_text=value_text,
        locked_fields=locked_fields,
        rubric=rubric,
    )

    field_obj.last_validation = result
    field_obj.save(update_fields=["last_validation", "updated_at"])

    payload = dict(result)
    payload["field_id"] = field_obj.id
    return JsonResponse(payload, status=200)


@login_required
@require_POST
def pde_field_lock(request):
    field_id_raw = (request.POST.get("field_id") or "").strip()
    if not field_id_raw:
        return JsonResponse({"error": "Missing field_id."}, status=400)

    try:
        field_id = int(field_id_raw)
    except ValueError:
        return JsonResponse({"error": "Invalid field_id."}, status=400)

    try:
        field = ProjectDefinitionField.objects.select_related("project").get(id=field_id)
    except ProjectDefinitionField.DoesNotExist:
        return JsonResponse({"error": "Field not found."}, status=404)

    if field.project.owner_id != request.user.id:
        return JsonResponse({"error": "Forbidden."}, status=403)

    last = field.last_validation or {}
    verdict = (last.get("verdict") or "").upper()
    if verdict != "PASS":
        return JsonResponse({"error": "Cannot lock: last verdict is not PASS."}, status=400)

    field.status = "PASS_LOCKED"
    field.locked_at = timezone.now()
    field.locked_by = request.user
    field.save(update_fields=["status", "locked_at", "locked_by", "updated_at"])

    return JsonResponse(
        {
            "field_id": field.id,
            "field_key": field.field_key,
            "status": field.status,
            "locked_at": field.locked_at.isoformat(),
        },
        status=200,
    )


@login_required
@require_POST
def pde_create_project_cko(request):
    """
    Create the first Project CKO doc from locked baseline fields.

    POST inputs:
    - project_id: int

    Output:
    - doc_text: combined document
    - knowledge_object_id: optional (None if not stored)
    """
    project_id_raw = (request.POST.get("project_id") or "").strip()
    if not project_id_raw:
        return JsonResponse({"error": "Missing project_id."}, status=400)

    try:
        project_id = int(project_id_raw)
    except ValueError:
        return JsonResponse({"error": "Invalid project_id."}, status=400)

    project = Project.objects.filter(id=project_id, owner=request.user).first()
    if not project:
        return JsonResponse({"error": "Project not found."}, status=404)

    locked = _get_locked_fields_for_project(project_id)

    missing = [k for k in BASELINE_L1_MUST_KEYS if not (locked.get(k) or "").strip()]
    if missing:
        return JsonResponse(
            {"error": "Missing locked baseline fields.", "missing": missing},
            status=400,
        )

    doc_text = _build_project_cko_document(project, locked)
    canonical_summary = _canonical_summary_10_words(locked.get("intent.primary_goal", ""))

    ko_id = _store_project_cko_if_possible(project, request.user, doc_text, canonical_summary)
    project.defined_cko = cko
    project.defined_at = timezone.now()
    project.defined_by = actor_user
    project.save(update_fields=["defined_cko", "defined_at", "defined_by", "updated_at"])


    return JsonResponse(
        {
            "project_id": project.id,
            "doc_text": doc_text,
            "knowledge_object_id": ko_id,
        },
        status=200,
    )

@login_required
def pde_home(request, project_id: int):
    project = Project.objects.filter(id=project_id, owner=request.user).first()
    if not project:
        return render(request, "404.html", status=404)

    LABELS = {
        "intent.primary_goal": "Primary Intent",
        "success.acceptance_test": "Success Criteria",
        "constraints.primary_constraint": "Primary Constraint",
        "authority.decision_owner": "Decision Owner",
    }

    BASELINE_ORDER = [
        "intent.primary_goal",
        "success.acceptance_test",
        "constraints.primary_constraint",
        "authority.decision_owner",
    ]

    fields = list(
        ProjectDefinitionField.objects
        .filter(project_id=project_id)
    )

    fields.sort(key=lambda f: BASELINE_ORDER.index(f.field_key))
        
    return render(
            request,
            "projects/pde_home.html",
            {
                "project": project,
                "fields": fields,
                "labels": LABELS,
            },
    )
@login_required
@require_POST
def pde_commit(request, project_id: int):
    project = get_object_or_404(Project, id=project_id)

    # Owner-only (match pde_detail)
    if project.owner_id != request.user.id:
        messages.error(request, "You do not have permission to commit this project.")
        return redirect("projects:pde_detail", project_id=project.id)

    try:
        result = commit_project_definition(
            project=project,
            required_keys=pde_required_keys_for_defined(),
        )
    except Exception as e:
        messages.error(request, "Commit failed: " + str(e))
        return redirect("projects:pde_detail", project_id=project.id)

    # Commit now creates a DRAFT CKO. Route to preview for iterative accept/revise loop.
    return redirect("projects:cko_preview", project_id=project.id)
