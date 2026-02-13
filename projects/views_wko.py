# projects/views_wko.py

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from projects.models import Project, ProjectWKO
from projects.services.artefact_render import render_artefact_html
from projects.services_project_membership import can_view_project


@login_required
def wko_preview(request, project_id: int) -> HttpResponse:
    project = get_object_or_404(Project, id=project_id)
    if not can_view_project(project, request.user):
        messages.error(request, "You do not have permission to view this project.")
        return redirect("accounts:dashboard")

    wko = (
        ProjectWKO.objects
        .filter(project=project)
        .order_by("-version")
        .first()
    )
    if not wko:
        messages.info(request, "No accepted WKO available yet.")
        return redirect("projects:ppde_detail", project_id=project.id)

    content_text = json.dumps(wko.content_json or {}, indent=2, ensure_ascii=True)
    content_html = render_artefact_html("WKO", wko.content_json or {})
    return render(
        request,
        "projects/wko_preview.html",
        {
            "project": project,
            "wko": wko,
            "content_text": content_text,
            "content_html": content_html,
        },
    )
