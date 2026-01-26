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
from chats.services.llm import build_image_parts_from_attachments
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
from projects.services.context_resolution import resolve_effective_context
from projects.services.llm_instructions import build_system_messages
from django.db.models import Exists, OuterRef
from django.db.models.functions import Coalesce
from accounts.models_avatars import Avatar
from projects.services.llm_instructions import PROTOCOL_LIBRARY



User = get_user_model()

# ------------------------------------------------------------
# Admin View on Sidebar
# ------------------------------------------------------------

@login_required
def admin_hub(request):
    return render(request, "accounts/admin_hub.html")

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

    # --------------------------------------------------
    # Deletion rule:
    # - USER messages => block
    # - ASSISTANT messages allowed ONLY if handshake
    # --------------------------------------------------

    user_n = ChatMessage.objects.filter(
        chat=chat,
        role__iexact="USER",
    ).count()

    asst_qs = ChatMessage.objects.filter(
        chat=chat,
        role__iexact="ASSISTANT",
    )

    handshake_qs = asst_qs.filter(raw_text__startswith="Hello")
    asst_real_n = asst_qs.count() - handshake_qs.count()

    if user_n > 0 or asst_real_n > 0:
        messages.error(request, "Chat contains messages and cannot be deleted.")
        return redirect("accounts:project_chat_list", p.id)

    # Safe to delete
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
# Dashboard (Projects -> Chats -> Settings) — tiles only
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
    active_chat_id = request.session.get("rw_active_chat_id")


    return render(
        request,
        "accounts/dashboard.html",
        {
            "projects": projects,
            "active_project": active_project,

            # tiles
            "recent_projects": recent_projects,
            "recent_chats": recent_chats,
            "can_override_chat": bool(active_chat_id),

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

    # Safety: do not delete if ANY real chat content exists (USER messages)
    has_real_msgs = ChatMessage.objects.filter(
        chat__project=p,
        role__iexact="USER",
    ).exists()

    if has_real_msgs:
        messages.error(request, "Project contains chats with messages and cannot be deleted.")
        return redirect("accounts:project_config_list")

    name = p.name or "(unnamed project)"

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
    # GET should never land here, but if it does (e.g. login redirect),
    # bounce to dashboard instead of hard-failing JSON.
    if request.method != "POST":
        return redirect("accounts:dashboard")

    chat_id = request.POST.get("chat_id")
    content = (request.POST.get("content") or "").strip()
    next_url = request.POST.get("next") or None

 #   print("DEBUG include_last_image POST =", repr(request.POST.get("include_last_image")))

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

    # 1) Store USER message
    ChatMessage.objects.create(
        chat=chat,
        role=ChatMessage.Role.USER,
        raw_text=content,
        answer_text=content,
        segment_meta={"confidence": "N/A", "parser_version": "user_v1"},
    )

    # 1b) Store new attachments (if any)
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

    # 2) Resolve context + build SYSTEM blocks
    chat_overrides = (
        request.session.get("rw_chat_overrides", {})
        .get(str(chat.id), {})
        or {}
    )
    session_overrides = request.session.get("rw_session_overrides", {}) or {}

    resolved = resolve_effective_context(
        project_id=project.id,
        user_id=user.id,
        session_overrides=session_overrides,
        chat_overrides=chat_overrides,
    )
    system_blocks = build_system_messages(resolved)

    # SYSTEM preview for UI observability/debugging
    request.session["rw_last_system_preview"] = "\n\n".join(system_blocks)
    request.session["rw_last_system_preview_chat_id"] = chat.id
    request.session["rw_last_system_preview_at"] = timezone.now().isoformat()
    request.session.modified = True

    # 3) Build conversation history (exclude SYSTEM and internal override-push assistant turns)
   # 3) LLM input: current message only (no history)
    llm_input = content


    # 4) Decide whether to include the last image (Option B) and build image_parts
    include_last_image = (request.POST.get("include_last_image") == "1")

    image_parts = []
    if include_last_image:
        img_atts = (
            ChatAttachment.objects
            .filter(chat=chat, content_type__startswith="image/")
            .order_by("-id")[:1]
        )
        image_parts = build_image_parts_from_attachments(reversed(list(img_atts)))

    has_images = bool(image_parts)

  #  print("DEBUG include_last_image =", include_last_image)
  #  print("DEBUG image_parts count =", len(image_parts))

    # 5) Call LLM (panes)
    panes = generate_panes(
        llm_input,
        image_parts=image_parts,
        system_blocks=system_blocks,
    )

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

    # 6) Update chat tile cache
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

    # Fullscreen view of chat
    fullscreen = request.GET.get("fullscreen") in ("1", "true", "yes")
    qs = request.GET.copy()
    qs.pop("fullscreen", None)
    qs_normal = qs.urlencode()
    qs_fs = request.GET.copy()
    qs_fs["fullscreen"] = "1"
    qs_fullscreen = qs_fs.urlencode()

    # Hide system: remove debug flag and any sys-* selection
    qs_hide = request.GET.copy()
    qs_hide.pop("system", None)
    qs_hide.pop("turn", None)
    qs_hide.pop("fullscreen", None)
    qs_hide_system = qs_hide.urlencode()

    # Set active chat for topbar counter etc
    request.session["rw_active_chat_id"] = chat.id
    request.session.modified = True

    ctx = build_chat_turn_context(request, chat)
    show_system = request.GET.get("system") in ("1", "true", "yes")


    # SYSTEM toggle
    show_system = request.GET.get("system") in ("1", "true", "yes")
    ctx = build_chat_turn_context(request, chat)
    show_system = request.GET.get("system") in ("1", "true", "yes")

    # ------------------------------------------------------------
    # SYSTEM PREVIEW (debug)
    # If a specific SYSTEM row is selected (turn=sys-<id>), show that
    # one message. Otherwise show the full SYSTEM thread.
    # ------------------------------------------------------------
    system_preview = ""
    selected_turn_id = request.GET.get("turn") or ""

    system_latest = {}

    m = None  # <-- define once, outside

    if show_system:
        if selected_turn_id.startswith("sys-"):
            try:
                sys_id = int(selected_turn_id.split("-", 1)[1])
            except ValueError:
                sys_id = None

            if sys_id is not None:
                m = (
                    ChatMessage.objects
                    .filter(chat=chat, role=ChatMessage.Role.SYSTEM, id=sys_id)
                    .first()
                )

    system_preview = (m.raw_text or "").strip() if m else ""

    return render(
        request,
        "accounts/chat_detail.html",
        {
            "active_project": chat.project,
            "active_chat": chat,
            "chat": chat,
            "fullscreen": fullscreen,
            "qs_normal": qs_normal,
            "qs_fullscreen": qs_fullscreen,
            "qs_hide_system": qs_hide_system,
            "show_system": show_system,
            "system_preview": system_preview,
            "system_latest": system_latest,

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
        pqs = accessible_projects_qs(user)
    else:
        pqs = (
            accessible_projects_qs(user)
            .filter(Q(owner=user) | Q(scoped_roles__user=user))
            .distinct()
        )

    projects = pqs.select_related("owner", "active_l4_config").order_by("name")
    active_project = get_object_or_404(accessible_projects_qs(user), pk=project_id)

    # Ensure session active project matches this page
    prev_project_id = request.session.get("rw_active_project_id")
    if str(prev_project_id) != str(active_project.id):
        request.session["rw_active_project_id"] = active_project.id
        request.session.pop("rw_active_chat_id", None)
        request.session.modified = True

    # Base queryset: chats in this project
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

    # Annotate counts used by UI
    # NOTE: adjust related_name "messages" if needed. If your FK has no related_name,
    # use "chatmessage" or "chatmessage_set". Your template already used "messages",
    # so we keep that.
    qs = qs.annotate(
        user_msg_count=Coalesce(
            Count("messages", filter=Q(messages__role__iexact="USER")), 0
        ),
        assistant_msg_count=Coalesce(
            Count("messages", filter=Q(messages__role__iexact="ASSISTANT")), 0
        ),
    )

    # Canonical: show delete only for SANDBOX chats with no USER messages
    # (SYSTEM + handshake-only are deletable)
    qs = qs.annotate(
        can_delete=Q(user_msg_count=0)
    )

    # What to show as "Turns" on this page:
    # Use completed-turn count later if you want; for now align with deletion logic.
    qs = qs.annotate(
        turn_count=Coalesce(
            Count("messages", filter=Q(messages__role__iexact="USER")), 0
        )
    )

    # Sorting
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

    # Pagination
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
    active_chat_id = request.session.get("rw_active_chat_id")
    return render(
        request,
        "accounts/config_menu.html",
        {
            "active_chat_id": active_chat_id,
            "can_override_chat": bool(active_chat_id),
        },
    )

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

        # Step 1: align Settings page with topbar effective session overrides (L4)
        session_map = {
            "COGNITIVE": "cognitive_avatar",
            "INTERACTION": "interaction_avatar",
            "PRESENTATION": "presentation_avatar",
            "EPISTEMIC": "epistemic_avatar",
            "PERFORMANCE": "performance_avatar",
            "CHECKPOINTING": "checkpointing_avatar",
        }

        for cat_key, field_name in session_map.items():
            override_id = request.session.get(f"rw_l4_override_{cat_key}")
            if override_id:
                form.fields[field_name].initial = override_id

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
# Avatar Chat overrides -- Temporarily overrides user Avatar Settings
# ------------------------------------------------------------
LANGUAGE_CODE_CHOICES = [
    ("en-GB", "English (UK) — en-GB"),
    ("en-US", "English (US) — en-US"),
]

LANGUAGE_VARIANT_CHOICES = [
    ("British English", "British English"),
    ("American English", "American English"),
]

def _parse_latest_axes_from_system_messages(sys_msgs):
    """
    Build a "latest per axis" snapshot from SYSTEM message history.
    Heuristic:
    - Prefer explicit headers like "COGNITIVE -- ANALYST"
    - LANGUAGE uses "Requested language" / "Default language" lines
    7-bit ASCII only.
    """
    latest = {
        "LANGUAGE": None,
        "EPISTEMIC": None,
        "COGNITIVE": None,
        "INTERACTION": None,
        "PRESENTATION": None,
        "PERFORMANCE": None,
        "CHECKPOINTING": None,
    }

    for m in sys_msgs:
        raw = (m.raw_text or "").strip()
        if not raw:
            continue

        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if not lines:
            continue

        head = lines[0]

        # LANGUAGE block
        if head.startswith("LANGUAGE"):
            lang_val = None
            for ln in lines[1:8]:
                if "Requested language:" in ln:
                    lang_val = ln.split("Requested language:", 1)[1].strip()
                    break
                if "Default language:" in ln:
                    lang_val = ln.split("Default language:", 1)[1].strip()
            if lang_val:
                latest["LANGUAGE"] = {"value": lang_val, "id": m.id, "at": m.created_at}
            continue

        # AXIS -- VALUE style (we use "--" to avoid unicode em dash issues)
        if "--" in head:
            left, right = head.split("--", 1)
            axis = left.strip().upper()
            val = right.strip()
            if axis in latest and val:
                latest[axis] = {"value": val, "id": m.id, "at": m.created_at}
            continue

        # AXIS - VALUE (fallback)
        if " - " in head:
            left, right = head.split(" - ", 1)
            axis = left.strip().upper()
            val = right.strip()
            if axis in latest and val:
                latest[axis] = {"value": val, "id": m.id, "at": m.created_at}
            continue

    return latest


def _safe_avatar_name(avatar_id) -> str | None:
    """
    avatar_id may be None, '', '123', etc.
    Returns Avatar.name if resolvable, else None.
    """
    if not avatar_id:
        return None
    s = str(avatar_id)
    if not s.isdigit():
        return None
    av = Avatar.objects.filter(id=int(s)).only("name").first()
    return av.name if av else None


def _build_language_block(*, language: str, variant: str | None, code: str | None) -> str:
    lang = (language or "").strip() or "English"
    var = (variant or "").strip()
    c = (code or "").strip()

    lines = [
        "LANGUAGE",
        f"- Requested language: {lang}",
    ]

    if var:
        lines.append(f"- Variant: {var}")
    if c:
        lines.append(f"- Preferred language code: {c}")

    lines += [
        "- If you can write fluently in the requested language, do so.",
        "- If you cannot, fall back to English (British English) and say: "
        '"Falling back to English (British English)."',
        "- Language switching permitted when explicitly requested.",
    ]
    return "\n".join(lines)

@login_required
def chat_config_overrides(request):

    import hashlib

    chat_overrides = request.session.get("rw_chat_overrides", {}) or {}
    active_chat_id = request.session.get("rw_active_chat_id")
    key = str(active_chat_id) if active_chat_id else None
    per_chat = chat_overrides.get(key, {}) if key else {}

    if request.method == "POST" and request.POST.get("reset"):
        if key:
            chat_overrides.pop(key, None)
            request.session["rw_chat_overrides"] = chat_overrides

        for k in [
            "rw_l4_override_COGNITIVE",
            "rw_l4_override_INTERACTION",
            "rw_l4_override_PRESENTATION",
            "rw_l4_override_EPISTEMIC",
            "rw_l4_override_PERFORMANCE",
            "rw_l4_override_CHECKPOINTING",
        ]:
            request.session.pop(k, None)

        request.session.modified = True
        messages.success(request, "Temporary overrides cleared (chat + session).")
        return redirect("accounts:chat_config_overrides")

    # --- purge legacy language keys ---
    if key and per_chat:
        changed = False
        if "LANGUAGE_VARIANT" in per_chat:
            per_chat.pop("LANGUAGE_VARIANT", None)
            changed = True
        if "LANGUAGE_CODE" in per_chat:
            per_chat.pop("LANGUAGE_CODE", None)
            changed = True

        if changed:
            chat_overrides[key] = per_chat
            request.session["rw_chat_overrides"] = chat_overrides
            request.session.modified = True

    def _choices(cat: str):
        return (
            Avatar.objects
            .filter(category=cat, is_active=True)
            .order_by("name")
            .only("id", "name")
        )

    cognitive_choices = _choices("COGNITIVE")
    interaction_choices = _choices("INTERACTION")
    presentation_choices = _choices("PRESENTATION")
    epistemic_choices = _choices("EPISTEMIC")
    performance_choices = _choices("PERFORMANCE")
    checkpointing_choices = _choices("CHECKPOINTING")

    lang_name_current = per_chat.get("LANGUAGE_NAME") or ""

    if request.method == "POST":
        if not key:
            messages.error(request, "No active chat selected.")
            return redirect("accounts:chat_config_overrides")

        per_chat = chat_overrides.get(key, {}) or {}

        old_vals = {
            "COGNITIVE": per_chat.get("COGNITIVE"),
            "INTERACTION": per_chat.get("INTERACTION"),
            "PRESENTATION": per_chat.get("PRESENTATION"),
            "EPISTEMIC": per_chat.get("EPISTEMIC"),
            "PERFORMANCE": per_chat.get("PERFORMANCE"),
            "CHECKPOINTING": per_chat.get("CHECKPOINTING"),
            "LANGUAGE_NAME": per_chat.get("LANGUAGE_NAME"),
        }

        # language (name-only, allow blank)
        new_language_name = (request.POST.get("language_name") or "").strip() or None
        per_chat["LANGUAGE_NAME"] = new_language_name
        per_chat.pop("LANGUAGE_VARIANT", None)
        per_chat.pop("LANGUAGE_CODE", None)

        # avatars (IDs)
        new_vals = {
            "COGNITIVE": request.POST.get("cognitive_id") or None,
            "INTERACTION": request.POST.get("interaction_id") or None,
            "PRESENTATION": request.POST.get("presentation_id") or None,
            "EPISTEMIC": request.POST.get("epistemic_id") or None,
            "PERFORMANCE": request.POST.get("performance_id") or None,
            "CHECKPOINTING": request.POST.get("checkpointing_id") or None,
        }

        per_chat.update(new_vals)
        chat_overrides[key] = per_chat
        request.session["rw_chat_overrides"] = chat_overrides
        request.session.modified = True

        chat = ChatWorkspace.objects.filter(pk=int(active_chat_id)).first() if active_chat_id else None
        if chat:
            AXES = [
                ("COGNITIVE", "cognitive", "Analyst"),
                ("INTERACTION", "interaction", "Structured"),
                ("PRESENTATION", "presentation", "Laptop"),
                ("EPISTEMIC", "epistemic", "Canonical"),
                ("PERFORMANCE", "performance", "Focused"),
                ("CHECKPOINTING", "checkpointing", "Manual"),
            ]

            to_send_system_texts = []

            for store_key, lib_key, fallback in AXES:
                if new_vals[store_key] != old_vals[store_key]:
                    old_name = _safe_avatar_name(old_vals[store_key]) or fallback
                    new_name = _safe_avatar_name(new_vals[store_key]) or fallback

                    lib = PROTOCOL_LIBRARY.get(lib_key, {})
                    lines = (
                        lib.get(new_name)
                        or lib.get(fallback)
                        or [f"[{lib_key.upper()}] Unrecognised value: {new_name}"]
                    )

                    change_header = f"[CONFIG_CHANGE] {store_key}: {old_name} -> {new_name}"

                    system_text = "\n".join(
                        [change_header, ""] + lines + [
                            "",
                            "These settings are now authoritative and override the current settings.",
                        ]
                    )

                    # Store audit SYSTEM message
                    ChatMessage.objects.create(
                        chat=chat,
                        role=ChatMessage.Role.SYSTEM,
                        raw_text=system_text,
                    )

                    # Queue to send to LLM immediately
                    to_send_system_texts.append(system_text)

            # ---- immediate push to LLM with idempotency hardening ----
            if to_send_system_texts:
                # Idempotency signature for "double click" / resubmit
                sig_src = f"{chat.id}|" + "||".join(to_send_system_texts)
                push_sig = hashlib.sha1(sig_src.encode("utf-8")).hexdigest()

                last_sig = request.session.get("rw_last_override_push_sig")
                last_at_iso = request.session.get("rw_last_override_push_at")
                allow_push = True

                if last_sig == push_sig and last_at_iso:
                    try:
                        last_dt = timezone.datetime.fromisoformat(last_at_iso)
                        if timezone.now() - last_dt < timezone.timedelta(seconds=10):
                            allow_push = False
                    except Exception:
                        pass

                if allow_push:
                    try:
                        chat_overrides_now = (
                            request.session.get("rw_chat_overrides", {})
                            .get(str(chat.id), {})
                            or {}
                        )
                        session_overrides_now = request.session.get("rw_session_overrides", {}) or {}

                        resolved_now = resolve_effective_context(
                            project_id=chat.project_id,
                            user_id=request.user.id,
                            session_overrides=session_overrides_now,
                            chat_overrides=chat_overrides_now,
                        )

                        base_system_blocks = build_system_messages(resolved_now)
                        system_blocks = base_system_blocks + to_send_system_texts

                        internal_user = "Internal: apply the authoritative override above."

                        panes = generate_panes(
                            "\n\n".join(system_blocks) + "\n\n" + "User:\n" + internal_user
                        )

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
                            segment_meta={"parser_version": "llm_v1", "confidence": "HIGH", "override_push": True},
                        )

                        chat.last_output_snippet = (out_msg.output_text or "")[:280]
                        chat.last_output_at = timezone.now()
                        chat.save(update_fields=["last_output_snippet", "last_output_at", "updated_at"])

                        # Store idempotency stamp
                        request.session["rw_last_override_push_sig"] = push_sig
                        request.session["rw_last_override_push_at"] = timezone.now().isoformat()

                        # Optional system preview
                        request.session["rw_last_system_preview"] = "\n\n".join(system_blocks)
                        request.session["rw_last_system_preview_chat_id"] = chat.id
                        request.session["rw_last_system_preview_at"] = timezone.now().isoformat()

                        request.session.modified = True

                    except Exception as e:
                        messages.error(request, f"Overrides saved, but LLM push failed: {e}")

        messages.success(request, "Temporary overrides saved.")
        return redirect("accounts:chat_config_overrides")

    current = chat_overrides.get(key, {}) if key else {}

    return render(
        request,
        "accounts/config_chat_overrides.html",
        {
            "active_chat_id": active_chat_id,
            "chat_override_current": current,
            "language_name_current": lang_name_current,
            "cognitive_choices": cognitive_choices,
            "interaction_choices": interaction_choices,
            "presentation_choices": presentation_choices,
            "epistemic_choices": epistemic_choices,
            "performance_choices": performance_choices,
            "checkpointing_choices": checkpointing_choices,
        },
    )

# --------------------------------------------------------
# Attach Clipboard Picture in Chat
# ------------------------------------------------------------
@require_POST
@login_required
def chat_attachment_paste(request, chat_id: int):
    """
    Accept one pasted clipboard image and store as ChatAttachment.
    Returns JSON for UI.
    """
    chat = get_object_or_404(ChatWorkspace.objects.select_related("project"), pk=chat_id)

    f = request.FILES.get("file")
    if not f:
        return JsonResponse({"ok": False, "error": "No file provided."}, status=400)

    ctype = (getattr(f, "content_type", "") or "").lower()
    if not ctype.startswith("image/"):
        return JsonResponse({"ok": False, "error": "Only image paste is supported."}, status=400)

    att = ChatAttachment.objects.create(
        project=chat.project,
        chat=chat,
        uploaded_by=request.user,
        file=f,
        original_name=getattr(f, "name", "pasted-image"),
        content_type=ctype,
        size_bytes=getattr(f, "size", 0) or 0,
    )

    # If ChatAttachment.file is a FileField, this will usually be available:
    file_url = ""
    try:
        file_url = att.file.url  # may raise if storage has no url
    except Exception:
        file_url = ""

    return JsonResponse(
        {
            "ok": True,
            "attachment": {
                "id": att.id,
                "name": att.original_name,
                "content_type": att.content_type,
                "size_bytes": att.size_bytes,
                "url": file_url,
            },
        }
    )


# --------------------------------------------------------
# Delete Clipboard Picture in Chat
# ------------------------------------------------------------
@require_POST
@login_required
def chat_attachment_delete(request, attachment_id: int):
    qs = ChatAttachment.objects.filter(pk=attachment_id, uploaded_by=request.user)
    att = qs.first()
    if not att:
        return JsonResponse({"ok": True, "already_deleted": True})

    try:
        if att.file:
            att.file.delete(save=False)
    finally:
        att.delete()

    return JsonResponse({"ok": True})

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
