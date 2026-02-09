# -*- coding: utf-8 -*-
# projects/views_project.py

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from chats.models import ChatMessage, ChatWorkspace
from chats.services.chat_bootstrap import bootstrap_chat
from chats.services.cleanup import delete_empty_sandbox_chats
from chats.services.llm import generate_panes
from config.models import ConfigRecord, ConfigScope, ConfigVersion
from projects.models import Project, ProjectCKO, ProjectMembership, ProjectWKO
from projects.services.project_bootstrap import bootstrap_project
from projects.services_project_membership import accessible_projects_qs, is_project_manager, can_edit_committee


@login_required
def active_project_set(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)

    project_id = request.POST.get("project_id")
    if not project_id:
        messages.error(request, "No project selected.")
        return redirect(request.POST.get("next") or "accounts:dashboard")

    try:
        pid = int(project_id)
    except ValueError:
        messages.error(request, "Invalid project.")
        return redirect(request.POST.get("next") or "accounts:dashboard")

    active_project = get_object_or_404(accessible_projects_qs(request.user), pk=pid)

    request.session["rw_active_project_id"] = active_project.id
    request.session.modified = True

    return redirect(request.POST.get("next") or "accounts:dashboard")


# ------------------------------------------------------------
# Projects (home/create/delete/select/project_chat_list)
# ------------------------------------------------------------

@login_required
def project_home(request, project_id: int):
    project = get_object_or_404(Project, id=project_id)

    request.session["rw_active_project_id"] = project.id
    request.session.pop("rw_active_chat_id", None)
    request.session.modified = True

    if project.kind == Project.Kind.SANDBOX:
        return redirect("accounts:chat_browse")

    if project.defined_cko_id is None:
        return redirect("projects:pde_detail", project_id=project.id)

    return redirect("accounts:project_config_info", project_id=project.id)


@login_required
def project_create(request):
    User = get_user_model()

    class ProjectCreateForm(forms.ModelForm):
        contributors = forms.ModelMultipleChoiceField(
            queryset=User.objects.none(),
            required=False,
            label="Project Contributors",
            help_text="Optional. Contributors can propose PDE edits but cannot commit.",
            widget=forms.SelectMultiple(attrs={"class": "form-select"}),
        )

        class Meta:
            model = Project
            fields = ("name", "purpose", "kind", "primary_type", "mode")
            widgets = {
                "name": forms.TextInput(attrs={"class": "form-control"}),
                "purpose": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
                "kind": forms.Select(attrs={"class": "form-select form-select-sm"}),
                "primary_type": forms.Select(attrs={"class": "form-select form-select-sm"}),
                "mode": forms.Select(attrs={"class": "form-select form-select-sm"}),
            }
            labels = {
                "kind": "Definition path",
                "primary_type": "Project category",
                "mode": "Stage",
            }

        def __init__(self, *args, **kwargs):
            user = kwargs.pop("user", None)
            super().__init__(*args, **kwargs)
            qs = User.objects.filter(is_active=True).order_by("username")
            if user is not None:
                qs = qs.exclude(id=user.id)
            self.fields["contributors"].queryset = qs

        def clean(self):
            cleaned = super().clean()
            kind = cleaned.get("kind")
            contributors = cleaned.get("contributors") or []
            if kind == Project.Kind.SANDBOX and contributors:
                self.add_error("contributors", "Sandbox projects cannot have contributors.")
            return cleaned

    if request.method == "POST":
        form = ProjectCreateForm(request.POST, user=request.user)
        if form.is_valid():
            p = form.save(commit=False)
            p.owner = request.user
            p.save()

            bootstrap_project(project=p)

            contributors = form.cleaned_data.get("contributors") or []
            for u in contributors:
                if u.id == request.user.id:
                    continue
                ProjectMembership.objects.update_or_create(
                    project=p,
                    user=u,
                    role=ProjectMembership.Role.CONTRIBUTOR,
                    scope_type=ProjectMembership.ScopeType.PROJECT,
                    scope_ref="",
                    defaults={
                        "status": ProjectMembership.Status.ACTIVE,
                        "effective_to": None,
                    },
                )

            request.session["rw_active_project_id"] = p.id
            request.session.modified = True

            if p.kind == Project.Kind.SANDBOX:
                chat = bootstrap_chat(
                    project=p,
                    user=request.user,
                    title="Chat 1",
                    generate_panes_func=generate_panes,
                    session_overrides=(request.session.get("rw_session_overrides", {}) or {}),
                )
                request.session["rw_active_chat_id"] = chat.id
                request.session.modified = True
                messages.success(request, "Sandbox project created.")
                return redirect(reverse("accounts:chat_detail", args=[chat.id]))

            messages.success(request, "Project created. Define it in PDE to enable chats.")
            return redirect(reverse("projects:pde_detail", args=[p.id]))
    else:
        form = ProjectCreateForm(user=request.user)

    return render(request, "accounts/project_create.html", {"form": form})


@require_POST
@login_required
def project_delete(request, project_id: int):
    p = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)

    if p.kind != "SANDBOX":
        messages.error(request, "Only SANDBOX projects can be deleted.")
        return redirect("accounts:project_config_list")

    if not (request.user.is_superuser or p.owner_id == request.user.id):
        messages.error(request, "You do not have permission to delete this project.")
        return redirect("accounts:project_config_list")

    has_real_msgs = ChatMessage.objects.filter(
        chat__project=p,
        role__iexact="USER",
    ).exists()

    if has_real_msgs:
        messages.error(request, "Project contains chats with messages and cannot be deleted.")
        return redirect("accounts:project_config_list")

    name = p.name or "(unnamed project)"

    with transaction.atomic():
        Project.objects.filter(pk=p.pk).update(active_l4_config=None)

        delete_empty_sandbox_chats(project=p)

        scopes = ConfigScope.objects.filter(project=p)
        ConfigVersion.objects.filter(config__scope__in=scopes).delete()
        ConfigRecord.objects.filter(scope__in=scopes).delete()
        scopes.delete()

        p.delete()

    messages.success(request, f"Deleted SANDBOX project: {name}")
    return redirect("accounts:project_config_list")


@login_required
def project_select(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)

    request.session["rw_active_project_id"] = project.id
    request.session.pop("rw_active_chat_id", None)
    request.session.modified = True

    return redirect("accounts:project_home", project_id=project.id)


@login_required
def project_chat_list(request, project_id: int):
    user = request.user

    if user.is_superuser or user.is_staff:
        pqs = accessible_projects_qs(user)
    else:
        pqs = (
            accessible_projects_qs(user)
            .filter(Q(owner=user) | Q(scoped_roles__user=user))
            .distinct()
        )

    projects = pqs.select_related("owner", "active_l4_config").order_by("name")
    active_project = get_object_or_404(accessible_projects_qs(user), pk=project_id)

    prev_project_id = request.session.get("rw_active_project_id")
    if str(prev_project_id) != str(active_project.id):
        request.session["rw_active_project_id"] = active_project.id
        request.session.pop("rw_active_chat_id", None)
        request.session.modified = True

    qs = ChatWorkspace.objects.select_related("created_by").filter(project=active_project)

    status = request.GET.get("status")
    q = (request.GET.get("q") or "").strip()

    if status in (ChatWorkspace.Status.ACTIVE, ChatWorkspace.Status.ARCHIVED):
        qs = qs.filter(status=status)

    if q:
        qs = qs.filter(
            Q(title__icontains=q)
            | Q(last_output_snippet__icontains=q)
            | Q(created_by__username__icontains=q)
        )

    qs = qs.annotate(
        user_msg_count=Coalesce(Count("messages", filter=Q(messages__role__iexact="USER")), 0),
        assistant_msg_count=Coalesce(Count("messages", filter=Q(messages__role__iexact="ASSISTANT")), 0),
    ).annotate(
        can_delete=Q(user_msg_count=0),
        turn_count=Coalesce(Count("messages", filter=Q(messages__role__iexact="USER")), 0),
    )

    sort = request.GET.get("sort", "updated")
    direction = request.GET.get("dir", "desc")

    sort_map = {
        "title": "title",
        "owner": "created_by__username",
        "updated": "updated_at",
        "turns": "turn_count",
    }

    order_field = sort_map.get(sort, "updated_at")
    if direction == "desc":
        order_field = f"-{order_field}"

    qs = qs.order_by(order_field, "-id")

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "accounts/project_chat_list.html",
        {
            "projects": projects,
            "active_project": active_project,
            "page_obj": page_obj,
            "filters": {"status": status or "", "q": q},
            "sort": sort,
            "dir": direction,
        },
    )


@login_required
def project_config_list(request):
    user = request.user

    qs = accessible_projects_qs(user)

    sort = request.GET.get("sort", "name")
    direction = request.GET.get("dir", "asc")

    sort_map = {
        "name": "name",
        "owner": "owner__username",
        "profile": "active_l4_config__file_name",
        "updated": "updated_at",
    }
    order_field = sort_map.get(sort, "name")
    if direction == "desc":
        order_field = f"-{order_field}"

    projects = qs.select_related("owner", "active_l4_config").order_by(order_field, "name")

    p = Paginator(projects, 25)
    page_obj = p.get_page(request.GET.get("page"))
    projects_with_permissions = [(proj, is_project_manager(proj, user)) for proj in page_obj.object_list]

    return render(
        request,
        "accounts/config_project_list.html",
        {
            "projects_with_permissions": projects_with_permissions,
            "sort": sort,
            "dir": direction,
            "page_obj": page_obj,
        },
    )


@login_required
def project_config_info(request, project_id: int):
    active_project = get_object_or_404(
        accessible_projects_qs(request.user),
        pk=project_id,
    )

    accepted_cko = None
    cko_history = []
    latest_wko = None

    if active_project.defined_cko_id:
        accepted_cko = ProjectCKO.objects.filter(
            id=active_project.defined_cko_id
        ).first()

        cko_history = (
            ProjectCKO.objects
            .filter(project=active_project)
            .order_by("-version")
        )

    latest_wko = (
        ProjectWKO.objects
        .filter(project=active_project)
        .order_by("-version")
        .first()
    )

    User = get_user_model()
    can_edit_team = can_edit_committee(active_project, request.user)
    memberships = (
        ProjectMembership.objects
        .filter(
            project=active_project,
            status=ProjectMembership.Status.ACTIVE,
            effective_to__isnull=True,
        )
        .select_related("user")
        .order_by("user__username")
    )

    member_rows = []
    seen_ids = set()
    for m in memberships:
        role_label = "COMMITTER" if m.user_id == active_project.owner_id else m.role
        member_rows.append(
            {
                "user": m.user,
                "user_id": m.user_id,
                "role": m.role,
                "role_label": role_label,
                "is_committer": m.user_id == active_project.owner_id,
            }
        )
        seen_ids.add(m.user_id)

    if active_project.owner_id not in seen_ids and active_project.owner_id:
        member_rows.append(
            {
                "user": active_project.owner,
                "user_id": active_project.owner_id,
                "role": ProjectMembership.Role.OWNER,
                "role_label": "COMMITTER",
                "is_committer": True,
            }
        )
        seen_ids.add(active_project.owner_id)

    available_users = (
        User.objects
        .filter(is_active=True)
        .exclude(id__in=list(seen_ids))
        .order_by("username")
    )

    if request.method == "POST" and (request.POST.get("action") or "") == "committee_update":
        if not can_edit_team:
            messages.error(request, "Only the Project Committer can edit the committee.")
            return redirect("accounts:project_config_info", project_id=active_project.id)

        if active_project.kind == Project.Kind.SANDBOX:
            messages.error(request, "Sandbox projects cannot have contributors.")
            return redirect("accounts:project_config_info", project_id=active_project.id)

        committer_id_raw = (request.POST.get("committer_id") or "").strip()
        if not committer_id_raw.isdigit():
            messages.error(request, "Committer is required.")
            return redirect("accounts:project_config_info", project_id=active_project.id)

        committer_id = int(committer_id_raw)
        new_committer = User.objects.filter(id=committer_id, is_active=True).first()
        if not new_committer:
            messages.error(request, "Committer must be an active user.")
            return redirect("accounts:project_config_info", project_id=active_project.id)

        member_ids = [int(x) for x in request.POST.getlist("member_ids") if str(x).isdigit()]
        add_user_ids = [int(x) for x in request.POST.getlist("add_user_ids") if str(x).isdigit()]

        allowed_roles = {
            ProjectMembership.Role.CONTRIBUTOR,
            ProjectMembership.Role.MANAGER,
            ProjectMembership.Role.OBSERVER,
        }

        keep_ids = set()

        with transaction.atomic():
            old_owner_id = active_project.owner_id
            if new_committer.id != old_owner_id:
                active_project.owner = new_committer
                active_project.save(update_fields=["owner", "updated_at"])

            ProjectMembership.objects.update_or_create(
                project=active_project,
                user=new_committer,
                role=ProjectMembership.Role.OWNER,
                scope_type=ProjectMembership.ScopeType.PROJECT,
                scope_ref="",
                defaults={
                    "status": ProjectMembership.Status.ACTIVE,
                    "effective_to": None,
                },
            )

            for uid in member_ids:
                if uid == new_committer.id:
                    continue
                if request.POST.get(f"member_remove_{uid}") == "on":
                    continue
                role = (request.POST.get(f"member_role_{uid}") or "").strip()
                if role not in allowed_roles:
                    role = ProjectMembership.Role.CONTRIBUTOR
                keep_ids.add(uid)
                ProjectMembership.objects.update_or_create(
                    project=active_project,
                    user_id=uid,
                    role=role,
                    scope_type=ProjectMembership.ScopeType.PROJECT,
                    scope_ref="",
                    defaults={
                        "status": ProjectMembership.Status.ACTIVE,
                        "effective_to": None,
                    },
                )

            for uid in add_user_ids:
                if uid == new_committer.id:
                    continue
                keep_ids.add(uid)
                ProjectMembership.objects.update_or_create(
                    project=active_project,
                    user_id=uid,
                    role=ProjectMembership.Role.CONTRIBUTOR,
                    scope_type=ProjectMembership.ScopeType.PROJECT,
                    scope_ref="",
                    defaults={
                        "status": ProjectMembership.Status.ACTIVE,
                        "effective_to": None,
                    },
                )

            to_end = ProjectMembership.objects.filter(
                project=active_project,
                status=ProjectMembership.Status.ACTIVE,
                effective_to__isnull=True,
            ).exclude(user_id=new_committer.id)
            if keep_ids:
                to_end = to_end.exclude(user_id__in=list(keep_ids))

            to_end.update(
                status=ProjectMembership.Status.LEFT,
                effective_to=timezone.now(),
            )

        messages.success(request, "Committee updated.")
        return redirect("accounts:project_config_info", project_id=active_project.id)

    return render(
        request,
        "accounts/config_project_info.html",
        {
            "project": active_project,
            "accepted_cko": accepted_cko,
            "cko_history": cko_history,
            "active_wko": latest_wko,
            "member_rows": member_rows,
            "available_users": available_users,
            "can_edit_committee": can_edit_team,
        },
    )


@login_required
def project_config_definitions(request, project_id: int):
    active_project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    return render(request, "accounts/config_project_definitions.html", {"project": active_project})


@login_required
def project_config_edit(request, project_id):
    project = get_object_or_404(Project, id=project_id)

    if not is_project_manager(project, request.user):
        return redirect("accounts:project_config_list")

    if request.method == "POST":
        new_name = (request.POST.get("name") or "").strip()
        description = request.POST.get("description", "")
        if new_name:
            project.name = new_name
            project.description = description
            project.save()
            return redirect("accounts:project_config_list")
        error = "Project name cannot be empty."
    else:
        error = None

    return render(
        request,
        "accounts/config_project_edit.html",
        {"project": project, "error": error},
    )
