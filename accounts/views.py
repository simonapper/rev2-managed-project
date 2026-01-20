# -*- coding: utf-8 -*-
# accounts/views.py

from __future__ import annotations
from django.utils import timezone


from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import urlsafe_base64_decode
from django.urls import reverse

from accounts.forms import UserProfileDefaultsForm
from config.models import ConfigRecord, ConfigScope, ConfigVersion
from projects.models import Project
from uploads.models import ChatAttachment
from chats.services.llm import generate_panes
from uuid import uuid4
from django.db.models import Count
from chats.models import ChatMessage
from projects.services_project_membership import accessible_projects_qs, is_project_manager
from accounts.forms import ProjectOperatingProfileForm
from collections import OrderedDict
import json
from pathlib import Path
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.core.files.storage import default_storage
from chats.models import ChatWorkspace, ChatMessage
from uploads.models import ChatAttachment
from django.core.paginator import Paginator
from chats.services.turns import build_chat_turn_context
from chats.services.chat_bootstrap import bootstrap_chat
from django.views.decorators.http import require_POST
from django.db import transaction
from chats.services.cleanup import delete_empty_sandbox_chats

User = get_user_model()


# ------------------------------------------------------------
# Delete Empty Chats
# ------------------------------------------------------------

@require_POST
@login_required
def chat_delete(request, chat_id: int):
    chat = get_object_or_404(ChatWorkspace, pk=chat_id)

    # Project access
    p = chat.project
    if not (request.user.is_superuser or p.owner_id == request.user.id):
        messages.error(request, "No permission to delete this chat.")
        return redirect("accounts:project_chat_list", p.id)

    # SANDBOX only
    if p.kind != "SANDBOX":
        messages.error(request, "Chats can only be deleted in SANDBOX projects.")
        return redirect("accounts:project_chat_list", p.id)

    # Only delete if chat has no USER/ASSISTANT messages
    has_real_msgs = ChatMessage.objects.filter(
        chat=chat
    ).exclude(role="SYSTEM").exists()

    if has_real_msgs:
        messages.error(request, "Chat contains messages and cannot be deleted.")
        return redirect("accounts:project_chat_list", p.id)

    chat.delete()
    messages.success(request, "Empty chat deleted.")
    return redirect("accounts:project_chat_list", p.id)

# ------------------------------------------------------------
# Invite flow: set password from emailed link
# ------------------------------------------------------------
def set_password_from_invite(request, uidb64: str, token: str):
    try:
        uid = urlsafe_base64_decode(uidb64).decode("utf-8")
        user = User.objects.get(pk=uid)
    except (ValueError, User.DoesNotExist, TypeError, UnicodeDecodeError):
        user = None

    if user is None or not default_token_generator.check_token(user, token):
        raise Http404("Invalid invite link.")

    if request.method == "POST":
        form = SetPasswordForm(user, request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Password set. You can now log in.")
            return redirect("accounts:login")
    else:
        form = SetPasswordForm(user)

    return render(request, "accounts/set_password.html", {"form": form})

# ------------------------------------------------------------
# Dashboard (Projects → Chats → Settings) — tiles only
# ------------------------------------------------------------
@login_required
def dashboard(request):
    user = request.user

    projects = (
        accessible_projects_qs(user)
        .select_related("owner", "active_l4_config")
        .order_by("name")
    )

    active_project_id = request.session.get("rw_active_project_id")
    active_project = projects.filter(id=active_project_id).first() if active_project_id else None

    recent_projects = projects.order_by("-updated_at")[:5]

    recent_chats = (
        ChatWorkspace.objects
        .filter(project__in=projects)
        .select_related("project")
        .order_by("-updated_at")[:5]
    )

    return render(
        request,
        "accounts/dashboard.html",
        {
            "projects": projects,
            "active_project": active_project,

            # tiles
            "recent_projects": recent_projects,
            "recent_chats": recent_chats,

            # backwards-compat with your current dashboard template (if it loops over `chats`)
            "chats": recent_chats,
        },
    )

# ------------------------------------------------------------
# Project create
# ------------------------------------------------------------
@login_required
def project_create(request):
    from django import forms
    from django.contrib import messages

    class ProjectCreateForm(forms.ModelForm):
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

    if request.method == "POST":
        form = ProjectCreateForm(request.POST)
        if form.is_valid():
            p = form.save(commit=False)
            p.owner = request.user
            p.save()  # signals handle policy, membership, L4 config
            chat = bootstrap_chat(project=p, user=request.user, title="Chat 1")
            request.session["rw_active_chat_id"] = chat.id
            request.session["rw_active_project_id"] = p.id
            request.session.modified = True

            messages.success(request, "Project created.")
            return redirect(reverse("accounts:chat_detail", args=[chat.id]))
    else:
        form = ProjectCreateForm()

    return render(request, "accounts/project_create.html", {"form": form})


# ------------------------------------------------------------
# Project delete
# ------------------------------------------------------------
@require_POST
@login_required
def project_delete(request, project_id: int):
    # Must be accessible to the user
    p = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)

    # Safety: only SANDBOX deletions allowed
    if p.kind != "SANDBOX":
        messages.error(request, "Only SANDBOX projects can be deleted.")
        return redirect("accounts:project_config_list")

    # Safety: owner or superuser only
    if not (request.user.is_superuser or p.owner_id == request.user.id):
        messages.error(request, "You do not have permission to delete this project.")
        return redirect("accounts:project_config_list")

    name = p.name

    with transaction.atomic():
        # Clear protected FK so project-scoped ConfigRecords can be removed
        Project.objects.filter(pk=p.pk).update(active_l4_config=None)

        # Clean empty chats FIRST (SANDBOX only)
        delete_empty_sandbox_chats(project=p)

        # Remove project-scoped configs
        scopes = ConfigScope.objects.filter(project=p)
        ConfigVersion.objects.filter(config__scope__in=scopes).delete()
        ConfigRecord.objects.filter(scope__in=scopes).delete()
        scopes.delete()

        # Finally delete the project
        p.delete()



    messages.success(request, f"Deleted SANDBOX project: {name}")
    return redirect("accounts:project_config_list")

# ------------------------------------------------------------
# Chats
# ------------------------------------------------------------
@login_required
def chat_list(request):
    user = request.user

    # Accessible projects (same rule as project_config_list)
    if user.is_superuser or user.is_staff:
        pqs = accessible_projects_qs(request.user)
    else:
        pqs = accessible_projects_qs(request.user).filter(Q(owner=user) | Q(scoped_roles__user=user)).distinct()

    projects = pqs.select_related("owner", "active_l4_config").order_by("name")

    # Resolve active project from session, else first accessible
    active_project = None
    active_project_id = request.session.get("rw_active_project_id")

    if active_project_id:
        active_project = projects.filter(pk=active_project_id).first()

    if active_project is None:
        active_project = projects.first()
        if active_project:
            request.session["rw_active_project_id"] = active_project.id
            request.session.modified = True

    chats = []
    if active_project:
        chats = list(
            ChatWorkspace.objects.filter(
                project=active_project,
                status=ChatWorkspace.Status.ACTIVE,
            ).order_by("-updated_at", "-created_at")
        )

    return render(
        request,
        "accounts/chat_list.html",
        {
            "projects": projects,
            "active_project": active_project,
            "chats": chats,
        },
    )
# ------------------------------------------------------------
# Chat create (composer POST)
# ------------------------------------------------------------
@login_required
def chat_create(request):
    user = request.user
    projects = accessible_projects_qs(user).order_by("name")

    if request.method == "POST":
        title = (request.POST.get("title") or "").strip()
        project_id = request.POST.get("project")

        project = projects.filter(id=project_id).first()
        if not project or not title:
            messages.error(request, "Title and project are required.")
            return redirect("accounts:chat_create")

        # chat = ChatWorkspace.objects.create(
        #     project=project,
        #     title=title,
        #     status=ChatWorkspace.Status.ACTIVE,
        #     created_by=user,
        # )
        chat = bootstrap_chat(project=project, user=user, title=title)


        request.session["rw_active_project_id"] = project.id
        request.session["rw_active_chat_id"] = chat.id
        request.session.modified = True

        return redirect(reverse("accounts:chat_detail", args=[chat.id]))

    selected_project_id = request.GET.get("project")
    if selected_project_id is not None:
        try:
            selected_project_id = int(selected_project_id)
        except ValueError:
            selected_project_id = None

    return render(
        request,
        "accounts/chat_create.html",
        {
            "projects": projects,
            "selected_project_id": selected_project_id,
        },
    )


# ------------------------------------------------------------
# Chat Rename (composer POST)
# ------------------------------------------------------------
@login_required
def chat_rename(request, chat_id: int):
    if request.method != "POST":
        return redirect("accounts:chat_detail", chat_id=chat_id)

    chat = get_object_or_404(ChatWorkspace, id=chat_id)

    # Optional access check (recommended if you have it)
    projects = accessible_projects_qs(request.user)
    if not projects.filter(id=chat.project_id).exists():
        return redirect("accounts:chat_browse")

    title = (request.POST.get("title") or "").strip()
    if not title:
        messages.error(request, "Title cannot be empty.")
        return redirect("accounts:chat_detail", chat_id=chat_id)

    chat.title = title[:200]
    chat.save(update_fields=["title", "updated_at"])
    messages.success(request, "Chat renamed.")
    return redirect("accounts:chat_detail", chat_id=chat_id)


# ------------------------------------------------------------
# Chat message create (composer POST)
# ------------------------------------------------------------

@login_required
def chat_message_create(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)

    chat_id = request.POST.get("chat_id")
    content = (request.POST.get("content") or "").strip()
    next_url = request.POST.get("next") or None

    if not chat_id:
        messages.error(request, "No chat selected.")
        return redirect(next_url or "accounts:dashboard")

    try:
        cid = int(chat_id)
    except ValueError:
        messages.error(request, "Invalid chat.")
        return redirect(next_url or "accounts:dashboard")

    if not content:
        messages.error(request, "Message cannot be empty.")
        return redirect(next_url or f"{reverse('accounts:dashboard')}?chat={cid}")

    chat = get_object_or_404(ChatWorkspace.objects.select_related("project"), pk=cid)
    project = chat.project
    user = request.user

    request.session["rw_active_chat_id"] = chat.id
    request.session.modified = True

    # 1) Store the user message (single row)
    user_msg = ChatMessage.objects.create(
        chat=chat,
        role=ChatMessage.Role.USER,
        raw_text=content,
        answer_text=content,          # optional convenience
        segment_meta={"confidence": "N/A", "parser_version": "user_v1"},
    )

    # attachments unchanged (if you have ChatAttachment)
    for f in request.FILES.getlist("attachments"):
        ChatAttachment.objects.create(
            project=project,
            chat=chat,
            uploaded_by=user,
            file=f,
            original_name=getattr(f, "name", "upload"),
            content_type=getattr(f, "content_type", "") or "",
            size_bytes=getattr(f, "size", 0) or 0,
        )

    # 2) Generate panes, store ONE assistant message row
    panes = generate_panes(content)

    assistant_raw = (
        "ANSWER:\n"
        f"{panes.get('answer','')}\n\n"
        "REASONING:\n"
        f"{panes.get('reasoning','')}\n\n"
        "OUTPUT:\n"
        f"{panes.get('output','')}\n"
    )

    out_msg = ChatMessage.objects.create(
        chat=chat,
        role=ChatMessage.Role.ASSISTANT,
        raw_text=assistant_raw,
        answer_text=panes.get("answer", ""),
        reasoning_text=panes.get("reasoning", ""),
        output_text=panes.get("output", ""),
        segment_meta={"parser_version": "llm_v1", "confidence": "HIGH"},
    )

    # 3) Update chat tile cache from Output pane
    chat.last_output_snippet = (out_msg.output_text or "")[:280]
    chat.last_output_at = timezone.now()
    chat.save(update_fields=["last_output_snippet", "last_output_at", "updated_at"])

    return redirect(next_url or reverse("accounts:chat_detail", args=[chat.id]))

# ------------------------------------------------------------
# Chat Browser
# ------------------------------------------------------------

@login_required
def chat_browse(request):
    user = request.user

    # Accessible projects
    if user.is_superuser or user.is_staff:
        pqs = accessible_projects_qs(request.user)
    else:
        pqs = accessible_projects_qs(request.user).filter(Q(owner=user) | Q(scoped_roles__user=user)).distinct()

    projects = pqs.select_related("owner", "active_l4_config").order_by("name")

    # Active project (optional filter)
    active_project = None
    project_id = request.GET.get("project") or ""
    if project_id:
        try:
            pid = int(project_id)
            active_project = projects.filter(pk=pid).first()
        except ValueError:
            active_project = None

    # Base queryset
    qs = ChatWorkspace.objects.select_related("project", "created_by").filter(project__in=projects)
    qs = qs.annotate(
        turn_count=Count("messages", filter=Q(messages__role=ChatMessage.Role.USER))
    )


    if active_project:
        qs = qs.filter(project=active_project)

    # Filters
    status = request.GET.get("status") or ""
    q = (request.GET.get("q") or "").strip()

    if status in (ChatWorkspace.Status.ACTIVE, ChatWorkspace.Status.ARCHIVED):
        qs = qs.filter(status=status)

    if q:
        qs = qs.filter(
            Q(title__icontains=q)
            | Q(last_output_snippet__icontains=q)
            | Q(project__name__icontains=q)
            | Q(created_by__username__icontains=q)
        )

    # Sorting
    sort = request.GET.get("sort", "updated")
    direction = request.GET.get("dir", "desc")

    sort_map = {
        "title": "title",
        "project": "project__name",
        "owner": "created_by__username",
        "updated": "updated_at",
        "turns": "turn_count",
    }

    order_field = sort_map.get(sort, "updated_at")
    if direction == "desc":
        order_field = f"-{order_field}"

    qs = qs.order_by(order_field, "-created_at")

    # Pagination
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "accounts/chat_browse.html",
        {
            "projects": projects,
            "active_project": active_project,
            "page_obj": page_obj,
            "filters": {"project": project_id, "status": status, "q": q},
            "sort": sort,
            "dir": direction,
        },
    )
# ------------------------------------------------------------
# Chat Select
# ------------------------------------------------------------
@login_required
def chat_select(request, chat_id: int):

    chat = get_object_or_404(
        ChatWorkspace.objects.only("id", "project_id"),
        pk=chat_id,
    )

    # Access check: user must be able to access the chat's project
    get_object_or_404(accessible_projects_qs(request.user), pk=chat.project_id)

    request.session["rw_active_project_id"] = chat.project_id
    request.session["rw_active_chat_id"] = chat.id
    request.session.modified = True

    return redirect(f"/accounts/chats/{chat.id}/")

# ------------------------------------------------------------
# Chat Detail
# ------------------------------------------------------------
@login_required
def chat_detail(request, chat_id: int):
    chat = get_object_or_404(ChatWorkspace, id=chat_id)

    ctx = build_chat_turn_context(request, chat)

    return render(
        request,
        "accounts/chat_detail.html",
        {
            "active_project": chat.project,
            "active_chat": chat,
            "chat": chat,
            **ctx,
        },
    )
# ------------------------------------------------------------
# Project Browser
# ------------------------------------------------------------

@login_required
def project_chat_list(request, project_id: int):
    user = request.user

    # Accessible projects (same rule you already use)
    if user.is_superuser or user.is_staff:
        pqs = accessible_projects_qs(request.user)
    else:
        pqs = accessible_projects_qs(request.user).filter(Q(owner=user) | Q(scoped_roles__user=user)).distinct()

    projects = pqs.select_related("owner", "active_l4_config").order_by("name")

    active_project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)

    # NEW: ensure session active project matches this page
    prev_project_id = request.session.get("rw_active_project_id")
    if str(prev_project_id) != str(active_project.id):
        request.session["rw_active_project_id"] = active_project.id
        request.session.pop("rw_active_chat_id", None)
        request.session.modified = True

    # Chats in this project
    qs = ChatWorkspace.objects.select_related("created_by").filter(project=active_project)

    # Filters
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

    # Sorting
    sort = request.GET.get("sort", "updated")
    direction = request.GET.get("dir", "desc")

    sort_map = {
        "title": "title",
        "owner": "created_by__username",
        "updated": "updated_at",
    }

    order_field = sort_map.get(sort, "updated_at")
    if direction == "desc":
        order_field = f"-{order_field}"

    qs = (
        ChatWorkspace.objects
        .filter(project=active_project)
        .annotate(
            real_msg_count=Count(
                "messages",
                filter=~Q(messages__role="SYSTEM"),
            )
        )
    )


    # Pagination
    from django.core.paginator import Paginator
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


# ------------------------------------------------------------
# Select Project
# ------------------------------------------------------------

@login_required
def project_select(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)

    request.session["rw_active_project_id"] = project.id
    request.session.pop("rw_active_chat_id", None)
    request.session.modified = True

    return redirect("accounts:project_chat_list", project_id=project.id)


# ------------------------------------------------------------
# Config menu
# ------------------------------------------------------------
@login_required
def config_menu(request):
    return render(request, "accounts/config_menu.html")


# ------------------------------------------------------------
# Active project (session)
# ------------------------------------------------------------
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

    user = request.user
    active_project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
  
    request.session["rw_active_project_id"] = active_project.id
    request.session.modified = True

    return redirect(request.POST.get("next") or "accounts:dashboard")


# ------------------------------------------------------------
# User config (Level 1) - edit
# ------------------------------------------------------------
@login_required
def user_config_edit(request):
    profile = getattr(request.user, "profile", None)
    if profile is None:
        raise Http404("User profile not found. Run backfill / ensure profile creation.")

    if request.method == "POST":
        form = UserProfileDefaultsForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, "Global User Settings saved.")
            return redirect("accounts:user_config_user")
    else:
        form = UserProfileDefaultsForm(instance=profile)

    return render(request, "accounts/config_user_edit.html", {"form": form})


@login_required
def user_config_info(request):
    return render(request, "accounts/config_user_info.html")


@login_required
def user_config_definitions(request):
    return render(request, "accounts/config_user_definitions.html")


# ------------------------------------------------------------
# Project config (Level 4 operating profile)
# ------------------------------------------------------------


@login_required
def project_config_list(request):
    user = request.user

    # Base queryset
    qs = accessible_projects_qs(user)

    # Sorting
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

    # Annotate permissions
    projects_with_permissions = [(p, is_project_manager(p, user)) for p in projects]
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
    active_project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    return render(request, "accounts/config_project_info.html", {"project": active_project})


@login_required
def project_config_definitions(request, project_id: int):
    active_project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    return render(request, "accounts/config_project_definitions.html", {"project": active_project})

@login_required
def project_config_edit(request, project_id):
    project = get_object_or_404(Project, id=project_id)

    # Only allow managers/owners to edit
    if not is_project_manager(project, request.user):
        return redirect("accounts:project_config_list")

    if request.method == "POST":
        new_name = (request.POST.get("name") or "").strip()
        description = request.POST.get("description", "")
        if new_name:
            project.name = new_name
            project.description = description
            project.save()
            return redirect("accounts:project_config_list")  # back to list
        else:
            error = "Project name cannot be empty."
    else:
        error = None

    return render(
        request,
        "accounts/config_project_edit.html",
        {"project": project, "error": error},
    )
# --------------------------------------------------------
# Session overrides (AJAX) - keep minimal so URL resolves
# ------------------------------------------------------------
@login_required
def session_overrides_update(request):
    """
    Minimal endpoint so your JS can POST overrides.
    If you already implemented this elsewhere, keep your implementation.
    """
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)

    return JsonResponse({"ok": True})
