# projects/views_execution.py

from __future__ import annotations

from collections import defaultdict

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from projects.models import Project, ProjectExecutionTask, ProjectMembership
from projects.services_project_membership import can_edit_ppde, can_view_project


@login_required
def execution_board(request, project_id: int):
    project = get_object_or_404(Project, id=project_id)
    if not can_view_project(project, request.user):
        messages.error(request, "You do not have permission to view this project.")
        return redirect("accounts:dashboard")

    can_edit = can_edit_ppde(project, request.user)

    if request.method == "POST":
        if not can_edit:
            messages.error(request, "You do not have permission to edit tasks.")
            return redirect("projects:execution_board", project_id=project.id)
        task_id = (request.POST.get("task_id") or "").strip()
        if not task_id.isdigit():
            return redirect("projects:execution_board", project_id=project.id)
        task = get_object_or_404(ProjectExecutionTask, id=int(task_id), project=project)

        status = (request.POST.get("status") or "").strip().upper()
        if status in ProjectExecutionTask.Status.values:
            task.status = status

        owner_id = (request.POST.get("owner_id") or "").strip()
        if owner_id == "":
            task.owner = None
        elif owner_id.isdigit():
            task.owner_id = int(owner_id)

        notes = (request.POST.get("notes") or "").strip()
        task.notes = notes

        due_raw = (request.POST.get("due_date") or "").strip()
        if due_raw:
            try:
                task.due_date = timezone.datetime.fromisoformat(due_raw).date()
            except Exception:
                pass
        else:
            task.due_date = None

        task.save(update_fields=["status", "owner", "notes", "due_date", "updated_at"])
        messages.success(request, "Task updated.")
        return redirect("projects:execution_board", project_id=project.id)

    tasks = ProjectExecutionTask.objects.filter(project=project).order_by("stage_title", "id")
    grouped = defaultdict(list)
    version_set = set()
    for t in tasks:
        key = (t.stage_title or "(no stage)").strip()
        grouped[key].append(t)
        if t.source_wko_version:
            version_set.add(t.source_wko_version)

    member_ids = list(
        ProjectMembership.objects.filter(
            project=project,
            status=ProjectMembership.Status.ACTIVE,
            effective_to__isnull=True,
        ).values_list("user_id", flat=True)
    )
    if project.owner_id and project.owner_id not in member_ids:
        member_ids.append(project.owner_id)

    User = get_user_model()
    members = User.objects.filter(id__in=member_ids).order_by("username")

    return render(
        request,
        "projects/execution_board.html",
        {
            "project": project,
            "grouped_tasks": dict(grouped),
            "members": members,
            "can_edit": can_edit,
            "source_versions": sorted(version_set, reverse=True),
        },
    )
