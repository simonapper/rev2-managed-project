# -*- coding: utf-8 -*-
# accounts/views.py
# WHOLE FILE

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
from config.models import ConfigRecord, ConfigScope
from projects.models import Project
from uploads.models import ChatAttachment
from chats.services.llm import generate_panes
from uuid import uuid4
from django.db.models import Count
from chats.models import ChatMessage
from projects.services import accessible_projects_qs
from accounts.forms import ProjectOperatingProfileForm
from collections import OrderedDict




User = get_user_model()


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
# Dashboard
# ------------------------------------------------------------
@login_required
def dashboard(request):
    user = request.user

    projects = (
        accessible_projects_qs(user)
        .select_related("owner", "active_l4_config")
        .order_by("name")
    )

    active_project = None
    active_project_id = request.session.get("rw_active_project_id")
    if active_project_id:
        active_project = projects.filter(pk=active_project_id).first()

    if active_project is None:
        active_project = projects.first()
        if active_project:
            request.session["rw_active_project_id"] = active_project.id
            request.session.modified = True

    active_chat = None
    chats = []
    attachments = []
    turn_items = []
    active_turn = None
    sort = request.GET.get("sort") or "updated"

    if active_project:
        from chats.models import ChatWorkspace, ChatMessage
        from uploads.models import ChatAttachment

        order_map = {
            "updated": ("-updated_at", "-created_at"),
            "created": ("-created_at",),
            "title": ("title",),
        }
        ordering = order_map.get(sort, order_map["updated"])

        chats = list(
            ChatWorkspace.objects.filter(
                project=active_project,
                status=ChatWorkspace.Status.ACTIVE,
            ).order_by(*ordering)
        )

        chat_id = request.GET.get("chat")
        if chat_id:
            try:
                cid = int(chat_id)
            except ValueError:
                cid = None
            if cid:
                active_chat = next((c for c in chats if c.id == cid), None)

        if active_chat is None and chats:
            active_chat = chats[0]

        if active_chat:
            request.session["rw_active_chat_id"] = active_chat.id
            request.session.modified = True
        else:
            request.session.pop("rw_active_chat_id", None)
            request.session.modified = True

        if active_chat:
            attachments = list(ChatAttachment.objects.filter(chat=active_chat))

            msg_list = list(
                ChatMessage.objects.filter(chat=active_chat).order_by("created_at")
            )

            turns = OrderedDict()

            for m in msg_list:
                tid = (m.tool_metadata or {}).get("turn_id") or "legacy"
                t = turns.setdefault(
                    tid,
                    {
                        "turn_id": tid,
                        "input": [],
                        "answer": [],
                        "keyinfo": [],
                        "visuals": [],
                        "reasoning": [],
                        "output": [],
                        "created_at": None,
                    },
                )

                if t["created_at"] is None:
                    t["created_at"] = m.created_at

                if m.role == ChatMessage.Role.USER:
                    t["input"].append(m)
                elif m.channel == ChatMessage.Channel.ANSWER:
                    t["answer"].append(m)
                elif m.channel == ChatMessage.Channel.SOURCES:
                    t["keyinfo"].append(m)
                elif m.channel == ChatMessage.Channel.VISUALS:
                    t["visuals"].append(m)
                elif m.channel in (
                    ChatMessage.Channel.REASONING,
                    ChatMessage.Channel.ANALYSIS,
                    ChatMessage.Channel.META,
                ):
                    t["reasoning"].append(m)
                else:
                    t["output"].append(m)

            turn_items = list(turns.values())
            for i, t in enumerate(turn_items, start=1):
                t["number"] = i

            selected_turn_id = request.GET.get("turn")
            if selected_turn_id:
                active_turn = next((t for t in turn_items if t["turn_id"] == selected_turn_id), None)

            if active_turn is None and turn_items:
                active_turn = turn_items[-1]

            for i, t in enumerate(turn_items, start=1):
                t["number"] = i

            turn_sort = request.GET.get("turn_sort") or "number"
            turn_dir = request.GET.get("turn_dir") or "asc"

            key_map = {
                "number": lambda x: x.get("number", 0),
                "title": lambda x: (x.get("input")[0].content if x.get("input") else ""),
                "updated": lambda x: x.get("created_at"),
            }
            key_fn = key_map.get(turn_sort, key_map["number"])
            turn_items = sorted(turn_items, key=key_fn, reverse=(turn_dir == "desc"))


    return render(
        request,
        "accounts/dashboard.html",
        {
            "projects": projects,
            "active_project": active_project,
            "chats": chats,
            "active_chat": active_chat,
            "attachments": attachments,
            "turn_items": turn_items,
            "active_turn": active_turn,
            "sort": sort,
            "turn_sort": turn_sort,
            "turn_dir": turn_dir,
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

            request.session["rw_active_project_id"] = p.id
            request.session.modified = True

            messages.success(request, "Project created.")
            return redirect("accounts:project_config_list")
    else:
        form = ProjectCreateForm()

    return render(request, "accounts/project_create.html", {"form": form})

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
        from chats.models import ChatWorkspace
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
# Chat message create (composer POST)
# ------------------------------------------------------------
@login_required
def chat_message_create(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)

    from uuid import uuid4

    from chats.models import ChatWorkspace, ChatMessage
    from chats.services.llm import generate_panes

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

    turn_id = uuid4().hex

    user_msg = ChatMessage.objects.create(
        chat=chat,
        role=ChatMessage.Role.USER,
        channel=ChatMessage.Channel.ANSWER,
        content=content,
        tool_metadata={"turn_id": turn_id},
    )

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

    panes = generate_panes(content)

    ChatMessage.objects.create(
        chat=chat,
        role=ChatMessage.Role.ASSISTANT,
        channel=ChatMessage.Channel.ANSWER,
        content=panes["answer"],
        tool_metadata={"turn_id": turn_id, "parent": user_msg.id},
    )

    ChatMessage.objects.create(
        chat=chat,
        role=ChatMessage.Role.ASSISTANT,
        channel=ChatMessage.Channel.SOURCES,
        content=panes["key_info"],
        tool_metadata={"turn_id": turn_id, "parent": user_msg.id},
    )

    ChatMessage.objects.create(
        chat=chat,
        role=ChatMessage.Role.ASSISTANT,
        channel=ChatMessage.Channel.VISUALS,
        content=panes["visuals"],
        tool_metadata={"turn_id": turn_id, "parent": user_msg.id},
    )

    ChatMessage.objects.create(
        chat=chat,
        role=ChatMessage.Role.ASSISTANT,
        channel=ChatMessage.Channel.REASONING,
        content=panes["reasoning"],
        tool_metadata={"turn_id": turn_id, "parent": user_msg.id},
    )

    out_msg = ChatMessage.objects.create(
        chat=chat,
        role=ChatMessage.Role.ASSISTANT,
        channel=ChatMessage.Channel.COMMENTARY,
        content=panes["output"],
        tool_metadata={"turn_id": turn_id, "parent": user_msg.id},
    )

    chat.last_output_snippet = (out_msg.content or "")[:280]
    chat.last_output_at = timezone.now()
    chat.save(update_fields=["last_output_snippet", "last_output_at", "updated_at"])

    return redirect(next_url or f"{reverse('accounts:dashboard')}?chat={chat.id}")

# ------------------------------------------------------------
# Chat Browser
# ------------------------------------------------------------

@login_required
def chat_browse(request):
    user = request.user

    from chats.models import ChatWorkspace

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
    from django.core.paginator import Paginator
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


    # Chats in this project
    from chats.models import ChatWorkspace
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

    qs = qs.order_by(order_field, "-created_at")

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
  
    request.session["rw_active_project_id"] = project.id
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

    if user.is_superuser or user.is_staff:
        qs = accessible_projects_qs(request.user)
    else:
        qs = accessible_projects_qs(request.user).filter(Q(owner=user) | Q(scoped_roles__user=user)).distinct()

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

    return render(
        request,
        "accounts/config_project_list.html",
        {"projects": projects, "sort": sort, "dir": direction},
    )

@login_required
def project_config_edit(request, project_id: int):
    user = request.user
    active_project = get_object_or_404(
        accessible_projects_qs(user),
        pk=project_id,
    )

    allowed_qs = (
        ConfigRecord.objects.filter(
            level=ConfigRecord.Level.L4,
            status=ConfigRecord.Status.ACTIVE,
            scope__scope_type=ConfigScope.ScopeType.PROJECT,
            scope__project=active_project,
        )
        .select_related("scope")
        .order_by("file_name", "file_id")
    )

    if request.method == "POST":
        form = ProjectOperatingProfileForm(request.POST, instance=active_project)
        form.fields["active_l4_config"].queryset = allowed_qs
        if form.is_valid():
            form.save()
            messages.success(request, "Project Operating Profile saved.")
            return redirect("accounts:project_config_edit", project_id=active_project.id)
    else:
        form = ProjectOperatingProfileForm(instance=active_project)
        form.fields["active_l4_config"].queryset = allowed_qs

    return render(
        request,
        "accounts/config_project_edit.html",
        {
            "active_project": active_project,
            "project": active_project,  # <-- add this line
            "form": form,
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


# ------------------------------------------------------------
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
