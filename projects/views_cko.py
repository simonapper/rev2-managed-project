# projects/views_cko.py
# 7-bit ASCII.

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.shortcuts import get_object_or_404, redirect, render
from projects.models import Project, ProjectCKO
from projects.services.cko import accept_project_cko, ProjectCKOAcceptError
from projects.services.artefact_render import render_artefact_html
from projects.services.pde_commit import _render_project_cko_html
from projects.services_project_membership import accessible_projects_qs, is_project_committer


from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

def cko_preview(request, project_id: int):
    project = get_object_or_404(Project, pk=project_id)

    accepted_flag = (request.GET.get("accepted") or "").strip().lower() in ("1", "true", "yes")

    def _self_redirect():
        url = reverse("projects:cko_preview", kwargs={"project_id": project.id})
        if accepted_flag:
            url = url + "?accepted=1"
        return HttpResponseRedirect(url)

    session_key = "cko_help_log_" + str(project.id)
    auto_key = "cko_help_auto_open_" + str(project.id)

    cko_help_log = request.session.get(session_key, [])
    if not isinstance(cko_help_log, list):
        cko_help_log = []

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()

        if action == "help_clear":
            request.session[session_key] = []
            request.session[auto_key] = True
            request.session.modified = True
            return _self_redirect()

        if action == "help_ask":
            q = (request.POST.get("help_question") or "").strip()
            if q:
                cko_help_log.append({"role": "user", "text": q})
                cko_help_log.append({"role": "assistant", "text": "TODO: wire CKO help LLM response."})
                request.session[session_key] = cko_help_log
                request.session[auto_key] = True
                request.session.modified = True
            return _self_redirect()

    if accepted_flag:
        if not project.defined_cko_id:
            raise Http404("No accepted CKO for this project.")
        cko = ProjectCKO.objects.filter(id=project.defined_cko_id, project=project).first()
        if not cko:
            raise Http404("No accepted CKO found for this project.")
    else:
        cko = (
            ProjectCKO.objects.filter(project=project, status=ProjectCKO.Status.DRAFT)
            .order_by("-version")
            .first()
        )
        if not cko:
            raise Http404("No draft CKO to preview.")

    # Render body live from field snapshot (do NOT trust stored content_html for chrome/meta)
    locked_fields = cko.field_snapshot or {}
    cko_body_html = _render_project_cko_html(project=project, locked_fields=locked_fields)
    content_json = cko.content_json or {}
    content_json_text = ""
    content_json_html = ""
    if content_json:
        content_json_text = json.dumps(content_json, indent=2, ensure_ascii=True)
        content_json_html = render_artefact_html("CKO", content_json)

    cko_help_auto_open = bool(request.session.get(auto_key))
    if cko_help_auto_open:
        request.session.pop(auto_key, None)
        request.session.modified = True

    return render(
        request,
        "projects/cko_preview.html",
        {
            "project": project,
            "cko": cko,
            "cko_body_html": cko_body_html,
            "content_json_text": content_json_text,
            "content_json_html": content_json_html,
            "viewing_accepted": accepted_flag,
            "can_accept": is_project_committer(project, request.user),
            "rw_help_enabled": True,
            "rw_help_title": "CKO Help",
            "rw_help_hint": "Ask questions about this CKO.",
            "rw_help_post_url": request.get_full_path(),
            "rw_help_log": cko_help_log,
            "rw_help_auto_open": cko_help_auto_open,
        },
    )



@login_required
def cko_print(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)

    accepted_flag = (request.GET.get("accepted") or "").strip().lower() in ("1", "true", "yes")
    if accepted_flag:
        if not project.defined_cko_id:
            raise Http404("No accepted CKO for this project.")
        cko = ProjectCKO.objects.filter(id=project.defined_cko_id, project=project).first()
        if not cko:
            raise Http404("No accepted CKO found for this project.")
    else:
        cko = (
            ProjectCKO.objects.filter(project=project, status=ProjectCKO.Status.DRAFT)
            .order_by("-version")
            .first()
        )
        if not cko:
            raise Http404("No draft CKO to preview.")

    locked_fields = cko.field_snapshot or {}
    cko_body_html = _render_project_cko_html(project=project, locked_fields=locked_fields)
    content_json = cko.content_json or {}
    content_json_html = ""
    if content_json:
        content_json_html = render_artefact_html("CKO", content_json)

    return render(
        request,
        "projects/cko_print.html",
        {
            "project": project,
            "cko": cko,
            "cko_body_html": cko_body_html,
            "content_json_html": content_json_html,
            "viewing_accepted": accepted_flag,
        },
    )


@login_required
def cko_accept(request, project_id: int, cko_id: int):
    if request.method != "POST":
        raise Http404()

    project = get_object_or_404(Project, pk=project_id)
    cko = get_object_or_404(ProjectCKO, pk=cko_id, project=project)

    if not is_project_committer(project, request.user):
        messages.error(request, "Only the Project Committer can commit this project.")
        return redirect("projects:cko_preview", project_id=project.id)

    try:
        accept_project_cko(project=project, cko=cko, actor_user=request.user)
    except ProjectCKOAcceptError as e:
        messages.error(request, str(e))
        return redirect("projects:cko_preview", project_id=project.id)

    messages.success(request, "CKO accepted. Project is now defined.")
    return redirect("accounts:project_home", project_id=project.id)
