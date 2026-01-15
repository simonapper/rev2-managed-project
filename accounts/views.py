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
from config.models import ConfigRecord, ConfigScope
from projects.models import Project
from uploads.models import ChatAttachment
from chats.services.llm import generate_panes
from uuid import uuid4
from django.db.models import Count
from chats.models import ChatMessage
from projects.services import accessible_projects_qs, is_project_manager
from accounts.forms import ProjectOperatingProfileForm
from collections import OrderedDict
import json
from pathlib import Path
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.core.files.storage import default_storage
from chats.models import ChatWorkspace, ChatMessage
from uploads.models import ChatAttachment


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
# Chat Context Builder
# ------------------------------------------------------------

from collections import OrderedDict

def build_chat_turn_context(request, chat):
    attachments = list(ChatAttachment.objects.filter(chat=chat))

    msg_list = list(
        ChatMessage.objects.filter(chat=chat).order_by("created_at")
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
    active_turn = None
    if selected_turn_id:
        active_turn = next((t for t in turn_items if t["turn_id"] == selected_turn_id), None)
    if active_turn is None and turn_items:
        active_turn = turn_items[-1]

    turn_sort, turn_dir = normalise_turn_sort(request)

    key_map = {
        "number": lambda x: x.get("number", 0),
        "title": lambda x: (x.get("input")[0].content if x.get("input") else ""),
        "updated": lambda x: x.get("created_at"),
    }
    key_fn = key_map.get(turn_sort, key_map["number"])
    turn_items = sorted(turn_items, key=key_fn, reverse=(turn_dir == "desc"))

    # re-number after sort
    for i, t in enumerate(turn_items, start=1):
        t["number"] = i

    return {
        "attachments": attachments,
        "turn_items": turn_items,
        "active_turn": active_turn,
        "turn_sort": turn_sort,
        "turn_dir": turn_dir,
    }


def get_active_project_and_chats(request, user, projects):
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
    active_chat = None

    if active_project:
        chats = (
            ChatWorkspace.objects
            .filter(project__in=projects)
            .order_by("-updated_at")[:10]
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

    return active_project, chats, active_chat



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
    active_project, chats, active_chat = get_active_project_and_chats(request, user, projects)

    attachments = []
    turn_items = []
    active_turn = None
    turn_sort, turn_dir = normalise_turn_sort(request)
    sort = request.GET.get("sort") or "updated"

    if active_chat:
        ctx = build_chat_turn_context(request, active_chat)
        attachments = ctx["attachments"]
        turn_items = ctx["turn_items"]
        active_turn = ctx["active_turn"]
        turn_sort = ctx["turn_sort"]
        turn_dir = ctx["turn_dir"]


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
# Sorter Helper
# ------------------------------------------------------------

def normalise_turn_sort(request):
    sort = request.GET.get("turn_sort", "number")
    direction = request.GET.get("turn_dir", "asc")

    if sort not in {"number", "title", "updated"}:
        sort = "number"
    if direction not in {"asc", "desc"}:
        direction = "asc"

    return sort, direction
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

        chat = ChatWorkspace.objects.create(
            project=project,
            title=title,
            status=ChatWorkspace.Status.ACTIVE,
            created_by=user,
        )

        request.session["rw_active_project_id"] = project.id
        request.session["rw_active_chat_id"] = chat.id
        request.session.modified = True

        return redirect(reverse("accounts:chat_detail", args=[chat.id]))

    return render(
        request,
        "accounts/chat_create.html",
        {"projects": projects},
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

    return redirect(next_url or reverse("accounts:chat_detail", args=[chat.id]))

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
# Import Preview
# ------------------------------------------------------------

# @login_required
# def import_preview(request):
#     """
#     Step 1: Preview ChatGPT JSON import.
#     Shows user selection, uploaded file, and preview of chats.
#     """
#     users = User.objects.filter(is_active=True).order_by("username")
#     preview_data = None
#     uploaded_file_name = None

#     if request.method == "POST" and "file" in request.FILES:
#         file = request.FILES["file"]
#         uploaded_file_name = file.name
#         # Store in temporary location to keep between steps
#         file_path = default_storage.save(f"temp/{file.name}", file)
#         request.session["import_file_path"] = file_path

#         # Parse the file for preview
#         try:
#             preview_data = parse_chatgpt_file(default_storage.open(file_path))
#         except Exception as e:
#             messages.error(request, f"Error parsing file: {str(e)}")
#             preview_data = None

#     return render(
#         request,
#         "imports/preview_import.html",
#         {
#             "users": users,
#             "preview_data": preview_data,
#             "uploaded_file_name": uploaded_file_name,
#         },
#     )
# def parse_chatgpt_file(file):
#     import json
#     try:
#         return json.load(file)
#     except Exception:
#         return []

# @login_required
# def import_chatgpt_action(request):
#     """
#     Step 2: Actually import the selected file into the chosen project/user.
#     Uses session-stored file path from preview step.
#     """
#     file_path = request.session.get("import_file_path")
#     if not file_path:
#         messages.error(request, "No file selected for import. Please preview first.")
#         return redirect(reverse("accounts:import_preview"))

#     if request.method == "POST":
#         project_id = request.POST.get("project")
#         user_id = request.POST.get("user")

#         try:
#             project = Project.objects.get(id=project_id)
#         except Project.DoesNotExist:
#             messages.error(request, "Selected project does not exist.")
#             return redirect(reverse("accounts:import_preview"))

#         try:
#             user = User.objects.get(id=user_id)
#         except User.DoesNotExist:
#             messages.error(request, "Selected user does not exist.")
#             return redirect(reverse("accounts:import_preview"))

#         # Parse the file again for import
#         try:
#             chats = parse_chatgpt_file(default_storage.open(file_path))
#         except Exception as e:
#             messages.error(request, f"Error parsing file: {str(e)}")
#             return redirect(reverse("accounts:import_preview"))

#         # Here you would loop through chats and create objects in DB
#         imported_count = 0
#         for chat in chats:
#             # Replace this with your actual chat creation logic
#             # e.g., Chat.objects.create(project=project, user=user, **chat)
#             imported_count += 1

#         # Clean up temporary file
#         default_storage.delete(file_path)
#         del request.session["import_file_path"]

#         messages.success(request, f"Imported {imported_count} chats into project '{project.name}' for user '{user.username}'.")
#         return redirect(reverse("accounts:project_config_list"))

#     # If GET request, redirect to preview
#     return redirect(reverse("accounts:import_preview"))
# # ------------------------------------------------------------
# # Import Preview Detail
# # ------------------------------------------------------------

# @login_required
# def import_preview_detail(request, conv_id: str):
#     import json
#     from pathlib import Path
#     from django.conf import settings

#     p = Path(settings.BASE_DIR) / "imports" / "chatgpt-export.json"
#     data = json.loads(p.read_text(encoding="utf-8"))
#     conversations = data.get("conversations") or data

#     conv = next((c for c in conversations if (c.get("id") or c.get("conversation_id")) == conv_id), None)
#     if conv is None:
#         raise Http404()

#     # Build turns for preview only (very rough until we map your export precisely)
#     turns = []
#     # common shape in ChatGPT exports: mapping of nodes
#     mapping = conv.get("mapping") or {}
#     # pick nodes that have message content
#     for _node_id, node in mapping.items():
#         msg = (node or {}).get("message") or {}
#         author = (msg.get("author") or {}).get("role")
#         content = (msg.get("content") or {}).get("parts") or []
#         text = "\n".join([p for p in content if isinstance(p, str)]).strip()
#         if not text:
#             continue
#         turns.append({"author": author, "text": text})

#     # crude ordering fallback
#     turns = turns[:300]

#     return render(
#         request,
#         "accounts/import_preview_detail.html",
#         {"conv": conv, "turns": turns},
#     )
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

    return render(
        request,
        "accounts/config_project_list.html",
        {
            "projects_with_permissions": projects_with_permissions,
            "sort": sort,
            "dir": direction,
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
        return redirect("accounts:config_project_list")

    if request.method == "POST":
        new_name = request.POST.get("name")
        description = request.POST.get("description", "")
        if new_name:
            project.name = new_name
            project.description = description
            project.save()
            return redirect("accounts:config_project_list")  # back to list
        else:
            error = "Project name cannot be empty."
    else:
        error = None

    return render(
        request,
        "accounts/config_project_edit.html",
        {
            "project": project,
            "error": error,
        },
    )
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
