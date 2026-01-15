# -*- coding: utf-8 -*-
# projects/views.py

from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from projects.models import Project
from projects.services import is_project_manager

@login_required
def rename_project(request, project_id):
    project = get_object_or_404(Project, id=project_id)

    if not is_project_manager(project, request.user):
        return redirect("accounts:config_project_list")  # permission fallback

    if request.method == "POST":
        new_name = request.POST.get("name")
        if new_name:
            project.name = new_name
            project.save()
            # Redirect back to the project browser after renaming
            return redirect("accounts:config_project_list")

    return render(request, "projects/rename_project.html", {"project": project})
