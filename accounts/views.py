# -*- coding: utf-8 -*-
# accounts/views.py
#
# Reordered so Projects and Chats are grouped.
# Cleanup:
# - Remove duplicate imports.
# - Keep the later chat_rename, but add require_POST to enforce POST-only.
# - Remove duplicate @login_required on chat_message_create.
# - Keep behaviour otherwise unchanged.

from __future__ import annotations

import json

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q, Case, When, Value, IntegerField
from django.db.models.functions import Coalesce
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone


def _safe_int(val):
    try:
        return int(val)
    except Exception:
        return None

from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_decode
from django.views.decorators.http import require_POST

from accounts.forms import ProjectOperatingProfileForm, UserProfileDefaultsForm
from accounts.models_avatars import Avatar
from chats.models import ChatMessage, ChatWorkspace
from chats.services.cde_injection import build_cde_system_blocks
from chats.services.cde_loop import validate_cde_inputs
from chats.services.chat_bootstrap import bootstrap_chat
from chats.services.llm import _get_default_model_key, generate_panes, generate_text
from chats.services.llm import build_image_parts_from_attachments
from chats.services.turns import build_chat_turn_context
from config.models import SystemConfigPointers
from projects.models import ProjectPlanningPurpose, ProjectPlanningStage
from projects.services.context_resolution import resolve_effective_context
from projects.services.llm_instructions import PROTOCOL_LIBRARY, build_system_messages
from projects.services_project_membership import accessible_projects_qs
from uploads.models import ChatAttachment


User = get_user_model()

ALLOWED_MODELS = [
    ("gpt-5.1", "gpt-5.1"),
    ("gpt-5-mini", "gpt-5-mini"),
    ("gpt-5-nano", "gpt-5-nano"),
    ("gpt-4.1", "gpt-4.1"),
    ("gpt-4.1-mini", "gpt-4.1-mini"),
    ("gpt-4.1-nano", "gpt-4.1-nano"),
    ("o3", "o3"),
    ("o4-mini", "o4-mini"),
    ("gpt-4o", "gpt-4o"),
]


class SystemConfigForm(forms.ModelForm):
    openai_model_default = forms.ChoiceField(
        choices=ALLOWED_MODELS,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )

    class Meta:
        model = SystemConfigPointers
        fields = ("openai_model_default",)


# ------------------------------------------------------------
# Admin / system
# ------------------------------------------------------------

@login_required
def admin_hub(request):
    pointers, _ = SystemConfigPointers.objects.get_or_create(id=1)

    if request.method == "POST":
        form = SystemConfigForm(request.POST, instance=pointers)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.updated_by = request.user
            obj.save()
            messages.success(request, "Default LLM model updated.")
            return redirect("accounts:admin_hub")
    else:
        form = SystemConfigForm(instance=pointers)

    return render(
        request,
        "accounts/admin_hub.html",
        {
            "form": form,
            "pointers": pointers,
        },
    )


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
# Dashboard + session selectors
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
        ChatWorkspace.objects.filter(project__in=projects)
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
            "recent_projects": recent_projects,
            "recent_chats": recent_chats,
            "can_override_chat": bool(active_chat_id),
            "chats": recent_chats,  # backwards compat
        },
    )




# ------------------------------------------------------------
# CDE helpers
# ------------------------------------------------------------

@require_POST
@login_required
def chat_suggest_goals(request):
    raw_goal = (request.POST.get("goal_text") or "").strip()
    if not raw_goal:
        return JsonResponse({"ok": False, "error": "Missing goal_text."}, status=400)

    system_blocks = [
        "You are helping a user phrase a managed chat goal.\n"
        "The user is often genuinely unsure what they want yet.\n"
        "\n"
        "Given a raw goal, produce exactly 3 alternative chat goals that:\n"
        "- preserve the user's intent and uncertainty\n"
        "- allow discovery and thinking\n"
        "- are honest about not knowing the outcome yet\n"
        "- are suitable for one chat\n"
        "\n"
        "Prefer meta-cognitive language such as:\n"
        "- clarify\n"
        "- surface\n"
        "- structure\n"
        "- define\n"
        "- make explicit\n"
        "\n"
        "Avoid fake project-management deliverables.\n"
        "Each alternative should be one sentence.\n"
        "Return as plain text with 3 lines, each starting with '- '.\n"
    ]

    user_msg = "Raw goal:\n" + raw_goal + "\n\nProduce 3 alternatives now."

    text = generate_text(
        system_blocks=system_blocks,
        messages=[{"role": "user", "content": user_msg}],
    )

    alts = []
    for line in (text or "").splitlines():
        t = line.strip()
        if t.startswith("- "):
            alts.append(t[2:].strip())

    if not alts and (text or "").strip():
        alts = [(text or "").strip()]

    return JsonResponse({"ok": True, "alternatives": alts[:3]})


@require_POST
@login_required
def chat_draft_cde(request):
    seed = (request.POST.get("seed_text") or "").strip()
    if not seed:
        return JsonResponse({"ok": False, "error": "Missing seed_text."}, status=400)

    from chats.services.cde import draft_cde_from_seed

    res = draft_cde_from_seed(generate_panes_func=generate_panes, seed_text=seed)
    if not res.get("ok"):
        return JsonResponse({"ok": False, "error": res.get("error") or "Draft failed."})

    return JsonResponse({"ok": True, "draft": res.get("draft")})


# ------------------------------------------------------------
# Chats
# ------------------------------------------------------------

@login_required
def chat_list(request):
    user = request.user

    if user.is_superuser or user.is_staff:
        pqs = accessible_projects_qs(request.user)
    else:
        pqs = accessible_projects_qs(request.user).filter(Q(owner=user) | Q(scoped_roles__user=user)).distinct()

    projects = pqs.select_related("owner", "active_l4_config").order_by("name")

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

        mode = (getattr(project, "mode", "") or "").strip().upper()
        kind = (getattr(project, "kind", "") or "").strip().upper()
        primary_type = (getattr(project, "primary_type", "") or "").strip().upper()
        name_is_sandbox = "SANDBOX" in (getattr(project, "name", "") or "").upper()

        is_sandbox = (
            (mode == "SANDBOX")
            or (kind == "SANDBOX")
            or (primary_type == "SANDBOX")
            or name_is_sandbox
        )

        if not is_sandbox:
            if project.defined_cko_id is None:
                messages.error(request, "Project is not defined. Complete PDE first.")
                return redirect("accounts:chat_create")

            ppde_started = (
                ProjectPlanningPurpose.objects.filter(project=project).exists()
                or ProjectPlanningStage.objects.filter(project=project).exists()
            )
            if not ppde_started:
                messages.error(request, "Start PPDE before creating chats.")
                return redirect("projects:ppde_detail", project_id=project.id)

        cde_mode = (request.POST.get("cde_mode") or "SKIP").strip().upper()
        cde_inputs = {
            "chat.goal": (request.POST.get("chat_goal") or "").strip(),
            "chat.success": (request.POST.get("chat_success") or "").strip(),
            "chat.constraints": (request.POST.get("chat_constraints") or "").strip(),
            "chat.non_goals": (request.POST.get("chat_non_goals") or "").strip(),
        }

        raw_cde_json = request.POST.get("cde_json")
        if raw_cde_json:
            try:
                cde_json = json.loads(raw_cde_json)
            except Exception:
                cde_json = {}
        else:
            cde_json = {}

        if cde_mode == "CONTROLLED":
            cde_result = validate_cde_inputs(
                generate_panes_func=generate_panes,
                user_inputs=cde_inputs,
            )

            if not bool(cde_result.get("ok")):
                return render(
                    request,
                    "accounts/chat_create.html",
                    {
                        "projects": projects,
                        "selected_project_id": project.id,
                        "sticky_title": title,
                        "sticky_cde_mode": cde_mode,
                        "sticky_chat_goal": cde_inputs.get("chat.goal", ""),
                        "sticky_chat_success": cde_inputs.get("chat.success", ""),
                        "sticky_chat_constraints": cde_inputs.get("chat.constraints", ""),
                        "sticky_chat_non_goals": cde_inputs.get("chat.non_goals", ""),
                        "cde_feedback": cde_result.get("first_blocker"),
                    },
                )

            locked_fields = cde_result.get("locked_fields") or {}
            cde_inputs = {
                "chat.goal": (locked_fields.get("chat.goal") or cde_inputs.get("chat.goal") or "").strip(),
                "chat.success": (locked_fields.get("chat.success") or cde_inputs.get("chat.success") or "").strip(),
                "chat.constraints": (locked_fields.get("chat.constraints") or cde_inputs.get("chat.constraints") or "").strip(),
                "chat.non_goals": (locked_fields.get("chat.non_goals") or cde_inputs.get("chat.non_goals") or "").strip(),
            }

        chat, _cde_result = bootstrap_chat(
            project=project,
            user=user,
            title=title,
            generate_panes_func=generate_panes,
            session_overrides=(request.session.get("rw_session_overrides", {}) or {}),
            cde_mode=cde_mode,
            cde_inputs=cde_inputs,
        )

        if cde_json:
            chat.cde_json = cde_json
            chat.save(update_fields=["cde_json", "updated_at"])

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


# Keep later chat_rename, but enforce POST-only
@require_POST
@login_required
def chat_rename(request, chat_id: int):
    chat = get_object_or_404(ChatWorkspace, id=chat_id)

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


@require_POST
@login_required
def chat_delete(request, chat_id: int):
    chat = get_object_or_404(ChatWorkspace, pk=chat_id)

    p = chat.project
    if not (request.user.is_superuser or p.owner_id == request.user.id):
        messages.error(request, "No permission to delete this chat.")
        return redirect("accounts:project_chat_list", p.id)

    user_n = ChatMessage.objects.filter(chat=chat, role__iexact="USER").count()
    asst_qs = ChatMessage.objects.filter(chat=chat, role__iexact="ASSISTANT")
    handshake_qs = asst_qs.filter(raw_text__startswith="Hello")
    asst_real_n = asst_qs.count() - handshake_qs.count()

    if user_n > 0 or asst_real_n > 0:
        messages.error(request, "Chat contains messages and cannot be deleted.")
        return redirect("accounts:project_chat_list", p.id)

    chat.delete()
    messages.success(request, "Empty chat deleted.")
    return redirect("accounts:chat_browse")


@require_POST
@login_required
def chat_message_create(request):
    chat_id = request.POST.get("chat_id")
    content = (request.POST.get("content") or "").strip()
    next_url = (request.POST.get("next") or "").strip()

    if not chat_id:
        messages.error(request, "No chat selected.")
        return redirect("accounts:dashboard")

    try:
        cid = int(chat_id)
    except ValueError:
        messages.error(request, "Invalid chat.")
        return redirect("accounts:dashboard")

    if not content:
        messages.error(request, "Message cannot be empty.")
        return redirect(reverse("accounts:chat_detail", args=[cid]))

    chat = get_object_or_404(ChatWorkspace.objects.select_related("project"), pk=cid)
    project = chat.project
    user = request.user

    request.session["rw_active_chat_id"] = chat.id
    request.session["rw_active_project_id"] = chat.project_id
    request.session.modified = True

    ChatMessage.objects.create(
        chat=chat,
        role=ChatMessage.Role.USER,
        raw_text=content,
        answer_text=content,
        segment_meta={"confidence": "N/A", "parser_version": "user_v1"},
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

    chat_overrides = (request.session.get("rw_chat_overrides", {}).get(str(chat.id), {}) or {})
    session_overrides = request.session.get("rw_session_overrides", {}) or {}

    resolved = resolve_effective_context(
        project_id=project.id,
        user_id=user.id,
        session_overrides=session_overrides,
        chat_overrides=chat_overrides,
    )
    system_blocks = build_system_messages(resolved)
    system_blocks.extend(build_cde_system_blocks(chat))

    request.session["rw_last_system_preview"] = "\n\n".join(system_blocks)
    request.session["rw_last_system_preview_chat_id"] = chat.id
    request.session["rw_last_system_preview_at"] = timezone.now().isoformat()
    request.session.modified = True

    include_last_image = (request.POST.get("include_last_image") == "1")
    image_parts = []
    if include_last_image:
        img_atts = (
            ChatAttachment.objects.filter(chat=chat, content_type__startswith="image/")
            .order_by("-id")[:1]
        )
        image_parts = build_image_parts_from_attachments(reversed(list(img_atts)))

    include_last_file = (request.POST.get("include_last_file") == "1")
    if include_last_file:
        att = ChatAttachment.objects.filter(chat=chat).order_by("-created_at").first()
        if att and (att.content_type or "").lower() in ("text/csv", "application/csv"):
            try:
                with att.file.open("rb") as fh:
                    raw = fh.read(200_000)
                csv_text = raw.decode("utf-8", errors="replace")
            except Exception:
                csv_text = ""
            if csv_text:
                content = content + "\n\n[ATTACHMENT: " + att.original_name + "]\n" + csv_text
            else:
                content = content + "\n\n[ATTACHMENT: " + att.original_name + " (unreadable)]"
        elif att:
            content = content + "\n\n[ATTACHMENT: " + att.original_name + " (unsupported type)]"
        else:
            content = content + "\n\n[ATTACHMENT: none]"

    sys_text = "\n\n".join([b for b in system_blocks if (b or "").strip()]).strip()
    if sys_text:
        ChatMessage.objects.create(
            chat=chat,
            role=ChatMessage.Role.SYSTEM,
            raw_text=sys_text,
            answer_text=sys_text,
            segment_meta={"parser_version": "system_v1", "confidence": "N/A"},
        )

    panes = generate_panes(
        content,
        image_parts=image_parts,
        system_blocks=system_blocks,
    )

    assistant_answer = (panes.get("answer") or "")
    assistant_reasoning = (panes.get("reasoning") or "")
    assistant_output = (panes.get("output") or "")
    assistant_raw = (assistant_answer or assistant_output or "").strip()

    out_msg = ChatMessage.objects.create(
        chat=chat,
        role=ChatMessage.Role.ASSISTANT,
        raw_text=assistant_raw,
        answer_text=assistant_answer,
        reasoning_text=assistant_reasoning,
        output_text=assistant_output,
        segment_meta={"parser_version": "llm_v1", "confidence": "HIGH"},
    )

    chat.last_answer_snippet = (out_msg.answer_text or out_msg.raw_text or "")[:280]
    chat.last_output_snippet = (out_msg.output_text or "")[:280]
    chat.last_output_at = timezone.now()
    chat.save(update_fields=["last_answer_snippet", "last_output_snippet", "last_output_at", "updated_at"])

    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)

    return redirect(reverse("accounts:chat_detail", args=[chat.id]))


@login_required
def chat_browse(request):
    user = request.user

    # Accessible projects (single canonical rule)
    projects = (
        accessible_projects_qs(user)
        .select_related("owner", "active_l4_config")
        .order_by("name")
    )

    # Project filter: default is ALL projects.
    # Only filter when a valid numeric project id is provided.
    project_param = (request.GET.get("project") or "").strip()

    # If your UI uses "all" explicitly, treat it as no filter.
    if project_param.lower() == "all":
        project_param = ""

    selected_project_id = None
    active_project = None
    project_filter_active = False
    pid_int = _safe_int(project_param)
    if pid_int is not None:
        selected_project_id = pid_int
        active_project = projects.filter(pk=selected_project_id).first()
        project_filter_active = True

    # If no explicit filter, fall back to session active project for highlighting
    if not project_filter_active:
        pid = request.session.get("rw_active_project_id")
        pid_int = _safe_int(pid)
        if pid_int is not None:
            active_project = projects.filter(pk=pid_int).first()

    # Keep global active project (topbar) in sync with the browse filter
    if project_filter_active and active_project is not None:
        request.session["rw_active_project_id"] = active_project.id
        request.session.modified = True


    qs = (
        ChatWorkspace.objects.select_related("project", "created_by")
        .filter(project__in=projects)
        .annotate(turn_count=Count("messages", filter=Q(messages__role=ChatMessage.Role.USER)))
    )

    if active_project is not None and not project_param:
        qs = qs.annotate(
            is_active_project=Case(
                When(project=active_project, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )

    # Apply project filter only when explicitly selected
    if project_filter_active and active_project is not None:
        qs = qs.filter(project=active_project)

    status = (request.GET.get("status") or "").strip()
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

    if active_project is not None and not project_param:
        qs = qs.order_by("-is_active_project", order_field, "-created_at")
    else:
        qs = qs.order_by(order_field, "-created_at")

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    palette = [
        "rgba(59, 130, 246, 0.16)",
        "rgba(16, 185, 129, 0.16)",
        "rgba(234, 179, 8, 0.18)",
        "rgba(168, 85, 247, 0.16)",
        "rgba(244, 63, 94, 0.14)",
        "rgba(14, 165, 233, 0.16)",
        "rgba(249, 115, 22, 0.16)",
        "rgba(34, 197, 94, 0.16)",
        "rgba(99, 102, 241, 0.16)",
        "rgba(148, 163, 184, 0.20)",
    ]
    for c in page_obj.object_list:
        idx = int(c.project_id) % len(palette)
        setattr(c, "row_color", palette[idx])

    return render(
        request,
        "accounts/chat_browse.html",
        {
            "projects": projects,
            "active_project": active_project,
            "page_obj": page_obj,
            "filters": {"project": project_param, "status": status, "q": q},
            "sort": sort,
            "dir": direction,
        },
    )


@login_required
def chat_select(request, chat_id: int):
    chat = get_object_or_404(ChatWorkspace.objects.only("id", "project_id"), pk=chat_id)

    get_object_or_404(accessible_projects_qs(request.user), pk=chat.project_id)

    request.session["rw_active_project_id"] = chat.project_id
    request.session["rw_active_chat_id"] = chat.id
    request.session.modified = True

    return redirect(f"/accounts/chats/{chat.id}/")


@login_required
def chat_detail(request, chat_id: int):
    chat = get_object_or_404(ChatWorkspace, id=chat_id)

    fullscreen = request.GET.get("fullscreen") in ("1", "true", "yes")
    qs = request.GET.copy()
    qs.pop("fullscreen", None)
    qs_normal = qs.urlencode()

    qs_fs = request.GET.copy()
    qs_fs["fullscreen"] = "1"
    qs_fullscreen = qs_fs.urlencode()

    qs_hide = request.GET.copy()
    qs_hide.pop("system", None)
    qs_hide.pop("turn", None)
    qs_hide.pop("fullscreen", None)
    qs_hide_system = qs_hide.urlencode()

    request.session["rw_active_chat_id"] = chat.id
    request.session.modified = True

    ctx = build_chat_turn_context(request, chat)

    show_system = request.GET.get("system") in ("1", "true", "yes")

    q_on = request.GET.copy()
    q_on["system"] = "1"
    qs_with_system = q_on.urlencode()

    q_off = request.GET.copy()
    q_off.pop("system", None)
    qs_no_system = q_off.urlencode()

    selected_turn_id = request.GET.get("turn") or ""

    m = None
    if show_system and selected_turn_id.startswith("sys-"):
        try:
            sys_id = int(selected_turn_id.split("-", 1)[1])
        except ValueError:
            sys_id = None

        if sys_id is not None:
            m = (
                ChatMessage.objects.filter(chat=chat, role=ChatMessage.Role.SYSTEM, id=sys_id)
                .first()
            )

    system_preview = (m.raw_text or "").strip() if m else ""

    atts = ctx.get("attachments") or []
    has_last_image = any((getattr(a, "source", "") or "").lower() == "paste" for a in atts)
    has_last_file = any((getattr(a, "source", "") or "").lower() == "filepicker" for a in atts)

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
            "qs_with_system": qs_with_system,
            "qs_no_system": qs_no_system,
            "system_preview": system_preview,
            "system_latest": {},
            "has_last_image": has_last_image,
            "has_last_file": has_last_file,
            **ctx,
        },
    )


# ------------------------------------------------------------
# Config menu + user/project config
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




# --------------------------------------------------------
# Avatar Chat overrides -- Temporarily overrides user Avatar Settings
# (kept as-is, only import cleanup above)
# ------------------------------------------------------------

LANGUAGE_CODE_CHOICES = [
    ("en-GB", "English (UK) - en-GB"),
    ("en-US", "English (US) - en-US"),
]

LANGUAGE_VARIANT_CHOICES = [
    ("British English", "British English"),
    ("American English", "American English"),
]


def _parse_latest_axes_from_system_messages(sys_msgs):
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

        if "--" in head:
            left, right = head.split("--", 1)
            axis = left.strip().upper()
            val = right.strip()
            if axis in latest and val:
                latest[axis] = {"value": val, "id": m.id, "at": m.created_at}
            continue

        if " - " in head:
            left, right = head.split(" - ", 1)
            axis = left.strip().upper()
            val = right.strip()
            if axis in latest and val:
                latest[axis] = {"value": val, "id": m.id, "at": m.created_at}
            continue

    return latest


def _safe_avatar_name(avatar_id) -> str | None:
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
        messages.success(request, "Temporary overrides cleared (chat + legacy session keys).")
        return redirect("accounts:chat_config_overrides")

    if key and per_chat:
        changed = False
        for legacy_k in [
            "COGNITIVE",
            "INTERACTION",
            "PRESENTATION",
            "EPISTEMIC",
            "PERFORMANCE",
            "CHECKPOINTING",
            "LANGUAGE_VARIANT",
            "LANGUAGE_CODE",
        ]:
            if legacy_k in per_chat:
                per_chat.pop(legacy_k, None)
                changed = True
        if changed:
            chat_overrides[key] = per_chat
            request.session["rw_chat_overrides"] = chat_overrides
            request.session.modified = True

    def _choices(cat: str):
        return Avatar.objects.filter(category=cat, is_active=True).order_by("name").only("id", "name")

    tone_choices = _choices("TONE")
    reasoning_choices = _choices("REASONING")
    approach_choices = _choices("APPROACH")
    control_choices = _choices("CONTROL")

    lang_name_current = per_chat.get("LANGUAGE_NAME") or ""

    def _avatar_name_from_id(raw_id):
        if raw_id is None:
            return None
        s = str(raw_id).strip()
        if not s.isdigit():
            return None
        av = Avatar.objects.filter(id=int(s)).only("name").first()
        return av.name if av else None

    if request.method == "POST":
        if not key:
            messages.error(request, "No active chat selected.")
            return redirect("accounts:chat_config_overrides")

        per_chat = chat_overrides.get(key, {}) or {}

        old_vals = {
            "tone": per_chat.get("tone"),
            "reasoning": per_chat.get("reasoning"),
            "approach": per_chat.get("approach"),
            "control": per_chat.get("control"),
            "LANGUAGE_NAME": per_chat.get("LANGUAGE_NAME"),
        }

        new_language_name = (request.POST.get("language_name") or "").strip() or None
        per_chat["LANGUAGE_NAME"] = new_language_name

        new_vals = {
            "tone": request.POST.get("tone_id") or None,
            "reasoning": request.POST.get("reasoning_id") or None,
            "approach": request.POST.get("approach_id") or None,
            "control": request.POST.get("control_id") or None,
        }

        per_chat.update(new_vals)
        chat_overrides[key] = per_chat
        request.session["rw_chat_overrides"] = chat_overrides
        request.session.modified = True

        chat = ChatWorkspace.objects.filter(pk=int(active_chat_id)).first() if active_chat_id else None
        if chat:
            changed_axes = []
            for axis in ("tone", "reasoning", "approach", "control"):
                if new_vals.get(axis) != old_vals.get(axis):
                    old_name = _avatar_name_from_id(old_vals.get(axis)) or "Default"
                    new_name = _avatar_name_from_id(new_vals.get(axis)) or "Default"
                    changed_axes.append((axis, old_name, new_name))

            if new_language_name != old_vals.get("LANGUAGE_NAME"):
                old_ln = old_vals.get("LANGUAGE_NAME") or "Default"
                new_ln = new_language_name or "Default"
                changed_axes.append(("language_name", old_ln, new_ln))

            if changed_axes:
                lines = ["[CONFIG_CHANGE] V2 overrides updated", ""]
                for axis, old_name, new_name in changed_axes:
                    lines.append(f"- {axis}: {old_name} -> {new_name}")
                lines.append("")
                lines.append("These settings are now authoritative and override the current settings.")
                audit_system_text = "\n".join(lines)

                ChatMessage.objects.create(
                    chat=chat,
                    role=ChatMessage.Role.SYSTEM,
                    raw_text=audit_system_text,
                )

            if changed_axes:
                sig_src = f"{chat.id}|{audit_system_text}"
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
                        chat_overrides_now = request.session.get("rw_chat_overrides", {}).get(str(chat.id), {}) or {}
                        session_overrides_now = request.session.get("rw_session_overrides", {}) or {}

                        resolved_now = resolve_effective_context(
                            project_id=chat.project_id,
                            user_id=request.user.id,
                            session_overrides=session_overrides_now,
                            chat_overrides=chat_overrides_now,
                        )

                        system_blocks = build_system_messages(resolved_now)

                        internal_user = "Internal: acknowledge the override is active. Say: Ready."
                        panes = generate_panes("\n\n".join(system_blocks) + "\n\n" + "User:\n" + internal_user)

                        assistant_raw = (
                            "ANSWER:\n"
                            + str(panes.get("answer", ""))
                            + "\n\nREASONING:\n"
                            + str(panes.get("reasoning", ""))
                            + "\n\nOUTPUT:\n"
                            + str(panes.get("output", ""))
                            + "\n"
                        )

                        out_msg = ChatMessage.objects.create(
                            chat=chat,
                            role=ChatMessage.Role.ASSISTANT,
                            raw_text=assistant_raw,
                            answer_text=panes.get("answer", ""),
                            reasoning_text=panes.get("reasoning", ""),
                            output_text=panes.get("output", ""),
                            segment_meta={
                                "parser_version": "llm_v1",
                                "confidence": "HIGH",
                                "override_push": True,
                            },
                        )

                        chat.last_output_snippet = (out_msg.output_text or "")[:280]
                        chat.last_output_at = timezone.now()
                        chat.save(update_fields=["last_output_snippet", "last_output_at", "updated_at"])

                        request.session["rw_last_override_push_sig"] = push_sig
                        request.session["rw_last_override_push_at"] = timezone.now().isoformat()

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
            "tone_choices": tone_choices,
            "reasoning_choices": reasoning_choices,
            "approach_choices": approach_choices,
            "control_choices": control_choices,
        },
    )


# --------------------------------------------------------
# Attachments
# ------------------------------------------------------------

@require_POST
@login_required
def chat_attachment_upload(request, chat_id: int):
    chat = get_object_or_404(ChatWorkspace.objects.select_related("project"), pk=chat_id)

    f = request.FILES.get("file")
    if not f:
        return JsonResponse({"ok": False, "error": "No file provided."}, status=400)

    ctype = (getattr(f, "content_type", "") or "").lower()
    source = (request.POST.get("source", "") or "").lower()

    if source == "paste" and not ctype.startswith("image/"):
        return JsonResponse({"ok": False, "error": "Only image paste is supported."}, status=400)

    att = ChatAttachment.objects.create(
        project=chat.project,
        chat=chat,
        uploaded_by=request.user,
        file=f,
        original_name=getattr(f, "name", "upload"),
        content_type=ctype,
        size_bytes=getattr(f, "size", 0) or 0,
    )

    try:
        file_url = att.file.url
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
# Session overrides (AJAX)
# ------------------------------------------------------------

@login_required
def session_overrides_update(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    return JsonResponse({"ok": True})
