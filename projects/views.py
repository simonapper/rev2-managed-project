# -*- coding: utf-8 -*-
# projects/views.py

from __future__ import annotations

from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from projects.services_project_membership import is_project_manager
from typing import Dict, List, Tuple
from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.urls import reverse
from accounts.models_avatars import Avatar
from projects.models import Project, UserProjectPrefs

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

# Membership/permission helper (adjust import if your function names differ)
try:
    from projects.services_project_membership import can_view_project
except Exception:
    can_view_project = None


AXES: List[Tuple[str, str, str]] = [
    # (Category enum value, label, UserProjectPrefs field name)
    ("COGNITIVE", "Cognitive", "cognitive_avatar"),
    ("INTERACTION", "Interaction", "interaction_avatar"),
    ("PRESENTATION", "Presentation", "presentation_avatar"),
    ("EPISTEMIC", "Epistemic", "epistemic_avatar"),
    ("PERFORMANCE", "Performance", "performance_avatar"),
    ("CHECKPOINTING", "Checkpointing", "checkpointing_avatar"),
]


def _user_can_view_project(project: Project, user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser or user.is_staff:
        return True
    if can_view_project is not None:
        return can_view_project(project, user)
    # Fallback (conservative): owner only
    return project.owner_id == user.id


@login_required
def project_preferences(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(Project, pk=project_id)

    if not _user_can_view_project(project, request.user):
        return HttpResponseForbidden("Not permitted.")

    prefs, _ = UserProjectPrefs.objects.get_or_create(project=project, user=request.user)

    # Build choices per axis
    choices: Dict[str, List[Avatar]] = {}
    for cat, _label, _field in AXES:
        qs = Avatar.objects.filter(category=cat, is_active=True).order_by("name")
        choices[cat] = list(qs)

    if request.method == "POST":
        # For each axis, accept:
        # - "" / "inherit" -> None (inherit from UserProfile)
        # - Avatar.id -> FK set
        changed = False

        for cat, _label, field in AXES:
            post_key = f"avatar_{cat}"
            raw = (request.POST.get(post_key) or "").strip()

            if raw in ("", "inherit", "none"):
                new_avatar = None
            else:
                try:
                    av_id = int(raw)
                except (TypeError, ValueError):
                    av_id = None
                new_avatar = None
                if av_id is not None:
                    new_avatar = Avatar.objects.filter(
                        id=av_id,
                        category=cat,
                        is_active=True,
                    ).first()

            current_avatar = getattr(prefs, field, None)
            current_id = current_avatar.id if current_avatar else None
            new_id = new_avatar.id if new_avatar else None

            if current_id != new_id:
                setattr(prefs, field, new_avatar)
                changed = True

        if changed:
            prefs.save()
            messages.success(request, "Preferences saved.")
        else:
            messages.info(request, "No changes.")

        # Keep the UI in sync: set active project in session
        request.session["rw_active_project_id"] = project.id
        request.session.modified = True

        return redirect(reverse("projects:project_preferences", kwargs={"project_id": project.id}))

    # For rendering: current selections (ids)
    current_ids: Dict[str, int | None] = {}
    for cat, _label, field in AXES:
        av = getattr(prefs, field, None)
        current_ids[cat] = av.id if av else None

    # Profile defaults (names) for "inherit" display
    profile = getattr(request.user, "profile", None)
    profile_defaults: Dict[str, str] = {}
    for cat, _label, field in AXES:
        name = "Default"
        if profile is not None:
            pav = getattr(profile, field, None)
            if pav is not None:
                name = pav.name
        profile_defaults[cat] = name

    # Ensure this project is the active one for topbar
    request.session["rw_active_project_id"] = project.id
    request.session.modified = True

    ctx = {
        "project": project,
        "axes": AXES,
        "choices": choices,
        "current_ids": current_ids,
        "profile_defaults": profile_defaults,
    }
    return render(request, "projects/project_preferences.html", ctx)
