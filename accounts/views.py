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

import hashlib
import io
import json
import logging
import re
import zipfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q, Case, When, Value, IntegerField
from django.db.models.functions import Coalesce
from django.http import Http404, JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone


def _safe_int(val):
    try:
        return int(val)
    except Exception:
        return None


def _strip_query_param(url: str, param_name: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        filtered = [(k, v) for (k, v) in pairs if k != param_name]
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(filtered, doseq=True),
                parts.fragment,
            )
        )
    except Exception:
        return raw

from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_decode
from django.views.decorators.http import require_POST

from accounts.forms import ProjectOperatingProfileForm, UserProfileDefaultsForm
from accounts.models_avatars import Avatar
from chats.models import ChatMessage, ChatRollupEvent, ChatWorkspace
from chats.services.cde_injection import build_cde_system_blocks
from chats.services.cde_loop import validate_cde_inputs
from chats.services.chat_bootstrap import bootstrap_chat
from chats.services.llm import _get_default_model_key, generate_panes, generate_text
from chats.services.llm import build_image_parts_from_attachments
from chats.services.pinning import (
    build_history_messages,
    build_pinned_system_block,
    count_active_window_messages,
    count_active_window_turns,
    rollup_segment,
    should_auto_rollup,
    undo_last_rollup,
)
from chats.services.turns import build_chat_turn_context
from config.models import SystemConfigPointers
from projects.models import (
    ProjectDefinitionField,
    ProjectPlanningPurpose,
    ProjectPlanningStage,
    ProjectPlanningMilestone,
    ProjectPlanningAction,
    ProjectPlanningRisk,
    ProjectAnchor,
    ProjectReviewChat,
    ProjectReviewStageChat,
    ProjectTopicChat,
)
from projects.services.artefact_render import render_artefact_html
from projects.services_text_normalise import normalise_sections
from projects.services_artefacts import normalise_pdo_payload, merge_execute_payload
from projects.services_execute_validator import validate_execute_update, merge_execute_update
from projects.services.context_resolution import resolve_effective_context
from projects.services.llm_instructions import PROTOCOL_LIBRARY, build_system_messages
from projects.services_project_membership import accessible_projects_qs, can_edit_pde, can_edit_ppde, is_project_committer
from uploads.models import ChatAttachment


User = get_user_model()

ALLOWED_MODELS = [
    ("gpt-5.2", "gpt-5.2"),
    ("gpt-5.1", "gpt-5.1"),
    ("gpt-5-mini", "gpt-5-mini"),
    ("gpt-5-nano", "gpt-5-nano"),
    # ("gpt-4.1", "gpt-4.1"),
    # ("gpt-4.1-mini", "gpt-4.1-mini"),
    # ("gpt-4.1-nano", "gpt-4.1-nano"),
]

ALLOWED_ANTHROPIC_MODELS = [
    ("claude-opus-4-6", "claude-opus-4-6"),
    ("claude-sonnet-4-5", "claude-sonnet-4-5"),
    ("claude-opus-4-5", "claude-opus-4-5"),
    ("claude-haiku-4-5", "claude-haiku-4-5"),
]

ALLOWED_DEEPSEEK_MODELS = [
    ("deepseek-chat", "deepseek-chat"),
    ("deepseek-reasoner", "deepseek-reasoner"),
]


class SystemConfigForm(forms.ModelForm):
    openai_model_default = forms.ChoiceField(
        choices=ALLOWED_MODELS,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    anthropic_model_default = forms.ChoiceField(
        choices=ALLOWED_ANTHROPIC_MODELS,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )

    class Meta:
        model = SystemConfigPointers
        fields = ("openai_model_default", "anthropic_model_default")


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
        user=request.user,
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

    def _generate_panes_for_user(*args, **kwargs):
        return generate_panes(*args, user=request.user, **kwargs)

    res = draft_cde_from_seed(generate_panes_func=_generate_panes_for_user, seed_text=seed)
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

        def _generate_panes_for_user(*args, **kwargs):
            return generate_panes(*args, user=user, **kwargs)

        if cde_mode == "CONTROLLED":
            cde_result = validate_cde_inputs(
                generate_panes_func=_generate_panes_for_user,
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
            generate_panes_func=_generate_panes_for_user,
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
    chat = get_object_or_404(
        ChatWorkspace,
        id=chat_id,
        project__in=accessible_projects_qs(request.user),
    )

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
    chat = get_object_or_404(
        ChatWorkspace.objects.select_related("project"),
        pk=chat_id,
        project__in=accessible_projects_qs(request.user),
    )

    p = chat.project
    if not (request.user.is_superuser or p.owner_id == request.user.id):
        messages.error(request, "No permission to delete this chat.")
        return redirect("accounts:project_chat_list", p.id)

    chat.delete()
    messages.success(request, "Chat deleted permanently.")
    return redirect("accounts:chat_browse")


@require_POST
@login_required
def chat_archive(request, chat_id: int):
    chat = get_object_or_404(
        ChatWorkspace.objects.select_related("project"),
        pk=chat_id,
        project__in=accessible_projects_qs(request.user),
    )
    p = chat.project
    if not (request.user.is_superuser or p.owner_id == request.user.id):
        messages.error(request, "No permission to archive this chat.")
        return redirect("accounts:chat_browse")

    if chat.status != ChatWorkspace.Status.ARCHIVED:
        chat.status = ChatWorkspace.Status.ARCHIVED
        chat.save(update_fields=["status", "updated_at"])

    active_chat_id = request.session.get("rw_active_chat_id")
    if str(active_chat_id) == str(chat.id):
        request.session.pop("rw_active_chat_id", None)
        request.session.modified = True

    messages.success(request, "Chat archived.")
    return redirect("accounts:chat_browse")


def _safe_zip_name(name: str) -> str:
    s = (name or "item").strip()
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    return s[:80] or "item"


_MAX_IMPORT_ZIP_BYTES = 50 * 1024 * 1024
_MAX_IMPORT_FILES = 1000
_MAX_IMPORT_MEMBER_BYTES = 25 * 1024 * 1024
_MAX_IMPORT_TOTAL_BYTES = 200 * 1024 * 1024
_MAX_IMPORT_RATIO = 200
_IMPORT_RATE_LIMIT_WINDOW_SECONDS = 60
_IMPORT_RATE_LIMIT_MAX = 6
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
_SECURITY_LOG = logging.getLogger("workbench.security")


def _validate_import_zip_safety(zf: zipfile.ZipFile) -> None:
    infos = zf.infolist()
    if len(infos) > _MAX_IMPORT_FILES:
        raise ValueError("ZIP has too many files.")

    total_uncompressed = 0
    for info in infos:
        size = int(getattr(info, "file_size", 0) or 0)
        compressed = int(getattr(info, "compress_size", 0) or 0)
        total_uncompressed += size

        if size > _MAX_IMPORT_MEMBER_BYTES:
            raise ValueError("ZIP member too large.")

        if compressed > 0 and (size / compressed) > _MAX_IMPORT_RATIO:
            raise ValueError("ZIP compression ratio too high.")

    if total_uncompressed > _MAX_IMPORT_TOTAL_BYTES:
        raise ValueError("ZIP uncompressed payload too large.")


def _safe_zip_read(zf: zipfile.ZipFile, member: str, *, max_bytes: int) -> bytes:
    info = zf.getinfo(member)
    size = int(getattr(info, "file_size", 0) or 0)
    if size > max_bytes:
        raise ValueError("ZIP member exceeds allowed size.")
    return zf.read(member)


def _check_import_rate_limit(*, user_id: int, scope: str) -> bool:
    key = f"rw:import-rate:{scope}:{user_id}"
    now_count = cache.get(key)
    if now_count is None:
        cache.set(key, 1, timeout=_IMPORT_RATE_LIMIT_WINDOW_SECONDS)
        return True
    if int(now_count) >= _IMPORT_RATE_LIMIT_MAX:
        return False
    try:
        cache.incr(key)
    except Exception:
        cache.set(key, int(now_count) + 1, timeout=_IMPORT_RATE_LIMIT_WINDOW_SECONDS)
    return True


def _record_security_event(request, event: str, **details) -> None:
    now = timezone.now()
    bucket = now.strftime("%Y%m%d%H")
    counter_key = f"rw:security:{event}:{bucket}"
    try:
        cache.incr(counter_key)
    except Exception:
        cache.set(counter_key, 1, timeout=60 * 60 * 48)

    user_id = getattr(getattr(request, "user", None), "id", None)
    ip = (
        request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        or request.META.get("REMOTE_ADDR", "")
    )
    _SECURITY_LOG.warning(
        "security_event=%s user_id=%s ip=%s path=%s details=%s",
        event,
        user_id,
        ip,
        request.path,
        details,
    )


@require_POST
@login_required
def chat_export(request, chat_id: int):
    chat = get_object_or_404(
        ChatWorkspace.objects.select_related("project"),
        pk=chat_id,
        project__in=accessible_projects_qs(request.user),
    )
    p = chat.project
    if not (request.user.is_superuser or p.owner_id == request.user.id):
        messages.error(request, "No permission to export this chat.")
        return redirect("accounts:chat_browse")

    messages_qs = ChatMessage.objects.filter(chat=chat).order_by("id")
    atts_qs = ChatAttachment.objects.filter(chat=chat).order_by("id")

    payload = {
        "type": "chat_export_v1",
        "chat": {
            "title": chat.title,
            "status": chat.status,
            "goal_text": chat.goal_text,
            "success_text": chat.success_text,
            "constraints_text": chat.constraints_text,
            "non_goals_text": chat.non_goals_text,
            "cde_is_locked": chat.cde_is_locked,
            "cde_json": chat.cde_json or {},
            "chat_overrides": chat.chat_overrides or {},
            "pinned_summary": chat.pinned_summary or "",
            "pinned_conclusion": chat.pinned_conclusion or "",
            "pinned_cursor_message_id": chat.pinned_cursor_message_id,
        },
        "project": {
            "name": p.name,
            "kind": p.kind,
        },
        "messages": [],
        "attachments": [],
    }

    for m in messages_qs:
        payload["messages"].append(
            {
                "role": m.role,
                "importance": m.importance,
                "raw_text": m.raw_text or "",
                "answer_text": m.answer_text or "",
                "reasoning_text": m.reasoning_text or "",
                "output_text": m.output_text or "",
                "segment_meta": m.segment_meta or {},
                "created_at": m.created_at.isoformat() if m.created_at else "",
            }
        )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for a in atts_qs:
            base = _safe_zip_name(a.original_name or f"attachment_{a.id}")
            arc_path = f"attachments/{a.id}_{base}"
            try:
                with a.file.open("rb") as fh:
                    zf.writestr(arc_path, fh.read())
            except Exception:
                continue
            payload["attachments"].append(
                {
                    "path": arc_path,
                    "original_name": a.original_name or "",
                    "content_type": a.content_type or "",
                    "size_bytes": int(a.size_bytes or 0),
                    "created_at": a.created_at.isoformat() if a.created_at else "",
                }
            )
        zf.writestr("chat.json", json.dumps(payload, ensure_ascii=True, indent=2))

    chat_title = chat.title
    chat.delete()
    messages.success(request, "Chat exported and deleted.")

    filename = _safe_zip_name(chat_title or "chat") + ".zip"
    resp = HttpResponse(zip_buffer.getvalue(), content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@require_POST
@login_required
def chat_import(request):
    if not _check_import_rate_limit(user_id=request.user.id, scope="chat"):
        _record_security_event(request, "chat_import_rate_limited")
        messages.error(request, "Too many import attempts. Please wait a minute and try again.")
        return redirect("accounts:chat_browse")

    f = request.FILES.get("chat_file")
    project_id = request.POST.get("project_id")

    if not f:
        messages.error(request, "Choose a chat export ZIP to import.")
        return redirect("accounts:chat_browse")
    if not str(project_id).isdigit():
        messages.error(request, "Select a target project for chat import.")
        return redirect("accounts:chat_browse")
    if int(getattr(f, "size", 0) or 0) > _MAX_IMPORT_ZIP_BYTES:
        _record_security_event(request, "chat_import_zip_too_large", size=int(getattr(f, "size", 0) or 0))
        messages.error(request, "Chat import ZIP is too large.")
        return redirect("accounts:chat_browse")

    project = get_object_or_404(accessible_projects_qs(request.user), pk=int(project_id))

    try:
        with zipfile.ZipFile(f) as zf:
            _validate_import_zip_safety(zf)
            raw = _safe_zip_read(zf, "chat.json", max_bytes=_MAX_IMPORT_MEMBER_BYTES)
            payload = json.loads(raw.decode("utf-8"))

            if payload.get("type") != "chat_export_v1":
                messages.error(request, "Unsupported chat export format.")
                return redirect("accounts:chat_browse")

            chat_data = payload.get("chat") or {}
            title = (chat_data.get("title") or "Imported chat").strip()[:250] or "Imported chat"

            chat = ChatWorkspace.objects.create(
                project=project,
                title=title,
                status=ChatWorkspace.Status.ACTIVE,
                created_by=request.user,
                goal_text=str(chat_data.get("goal_text") or ""),
                success_text=str(chat_data.get("success_text") or ""),
                constraints_text=str(chat_data.get("constraints_text") or ""),
                non_goals_text=str(chat_data.get("non_goals_text") or ""),
                cde_is_locked=bool(chat_data.get("cde_is_locked")),
                cde_json=chat_data.get("cde_json") if isinstance(chat_data.get("cde_json"), dict) else {},
                chat_overrides=chat_data.get("chat_overrides") if isinstance(chat_data.get("chat_overrides"), dict) else {},
                pinned_summary=str(chat_data.get("pinned_summary") or ""),
                pinned_conclusion=str(chat_data.get("pinned_conclusion") or ""),
                pinned_cursor_message_id=chat_data.get("pinned_cursor_message_id"),
                pinned_updated_at=timezone.now(),
            )

            for m in payload.get("messages") or []:
                if not isinstance(m, dict):
                    continue
                role = str(m.get("role") or "").upper()
                if role not in {ChatMessage.Role.USER, ChatMessage.Role.ASSISTANT, ChatMessage.Role.SYSTEM}:
                    continue
                importance = str(m.get("importance") or ChatMessage.Importance.NORMAL).upper()
                if importance not in {
                    ChatMessage.Importance.NORMAL,
                    ChatMessage.Importance.PINNED,
                    ChatMessage.Importance.IGNORE,
                }:
                    importance = ChatMessage.Importance.NORMAL
                ChatMessage.objects.create(
                    chat=chat,
                    role=role,
                    importance=importance,
                    raw_text=str(m.get("raw_text") or ""),
                    answer_text=str(m.get("answer_text") or ""),
                    reasoning_text=str(m.get("reasoning_text") or ""),
                    output_text=str(m.get("output_text") or ""),
                    segment_meta=m.get("segment_meta") if isinstance(m.get("segment_meta"), dict) else {},
                )

            for a in payload.get("attachments") or []:
                if not isinstance(a, dict):
                    continue
                arc_path = str(a.get("path") or "")
                if not arc_path:
                    continue
                try:
                    blob = _safe_zip_read(zf, arc_path, max_bytes=_MAX_IMPORT_MEMBER_BYTES)
                except Exception:
                    continue
                original_name = str(a.get("original_name") or "attachment.bin")
                cf = ContentFile(blob, name=original_name)
                ChatAttachment.objects.create(
                    project=project,
                    chat=chat,
                    uploaded_by=request.user,
                    file=cf,
                    original_name=original_name,
                    content_type=str(a.get("content_type") or ""),
                    size_bytes=int(a.get("size_bytes") or len(blob)),
                )
    except Exception as exc:
        _record_security_event(request, "chat_import_invalid_zip", error=str(exc)[:160])
        messages.error(request, f"Invalid chat export: {exc}")
        return redirect("accounts:chat_browse")

    request.session["rw_active_project_id"] = project.id
    request.session["rw_active_chat_id"] = chat.id
    request.session.modified = True

    messages.success(request, "Chat imported.")
    return redirect("accounts:chat_detail", chat_id=chat.id)


@require_POST
@login_required
def chat_message_create(request):
    chat_id = request.POST.get("chat_id")
    content = (request.POST.get("content") or "").strip()
    answer_mode = (request.POST.get("answer_mode") or "quick").strip().lower()
    if answer_mode not in {"quick", "full"}:
        answer_mode = "quick"
    next_url = _strip_query_param((request.POST.get("next") or "").strip(), "turn")

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

    chat = get_object_or_404(
        ChatWorkspace.objects.select_related("project"),
        pk=cid,
        project__in=accessible_projects_qs(request.user),
    )
    project = chat.project
    user = request.user

    persisted_overrides = (getattr(chat, "chat_overrides", {}) or {}).copy()
    if persisted_overrides.get("answer_mode") != answer_mode:
        persisted_overrides["answer_mode"] = answer_mode
        chat.chat_overrides = persisted_overrides
        chat.save(update_fields=["chat_overrides"])

    session_chat_overrides = request.session.get("rw_chat_overrides", {}) or {}
    session_per_chat = session_chat_overrides.get(str(chat.id), {}) or {}
    if session_per_chat.get("answer_mode") != answer_mode:
        session_per_chat["answer_mode"] = answer_mode
        session_chat_overrides[str(chat.id)] = session_per_chat
        request.session["rw_chat_overrides"] = session_chat_overrides
        request.session.modified = True

    request.session["rw_active_chat_id"] = chat.id
    request.session["rw_active_project_id"] = chat.project_id
    request.session.modified = True

    user_msg = ChatMessage.objects.create(
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

    chat_overrides = (
        request.session.get("rw_chat_overrides", {}).get(str(chat.id), {})
        or (getattr(chat, "chat_overrides", {}) or {})
    )
    session_overrides = request.session.get("rw_session_overrides", {}) or {}

    resolved = resolve_effective_context(
        project_id=project.id,
        user_id=user.id,
        session_overrides=session_overrides,
        chat_overrides=chat_overrides,
    )
    system_blocks = build_system_messages(resolved)
    pinned_block = build_pinned_system_block(chat)
    if pinned_block:
        system_blocks.append(pinned_block)
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

    binding = ProjectTopicChat.objects.filter(chat=chat).first()
    if binding:
        seed_msg = (
            ChatMessage.objects
            .filter(chat=chat, role=ChatMessage.Role.USER)
            .order_by("id")
            .first()
        )
        seed_text = (seed_msg.raw_text or "").strip() if seed_msg else ""
        if seed_text.startswith("Topic chat:"):
            content = "Seed context:\n" + seed_text + "\n\nUser message:\n" + content

    sys_text = "\n\n".join([b for b in system_blocks if (b or "").strip()]).strip()
    if sys_text:
        ChatMessage.objects.create(
            chat=chat,
            role=ChatMessage.Role.SYSTEM,
            raw_text=sys_text,
            answer_text=sys_text,
            segment_meta={"parser_version": "system_v1", "confidence": "N/A"},
        )

    history_messages = build_history_messages(
        chat,
        answer_mode=answer_mode,
        exclude_message_ids=[user_msg.id],
    )

    panes = generate_panes(
        content,
        image_parts=image_parts,
        system_blocks=system_blocks,
        history_messages=history_messages,
        user=request.user,
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

    if should_auto_rollup(chat, user=request.user):
        rollup_segment(chat, user=request.user, trigger=ChatRollupEvent.Trigger.AUTO)

    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)

    return redirect(reverse("accounts:chat_detail", args=[chat.id]))


@require_POST
@login_required
def message_set_importance(request, message_id: int):
    msg = get_object_or_404(ChatMessage.objects.select_related("chat", "chat__project"), pk=message_id)
    chat = msg.chat
    project = chat.project

    if not accessible_projects_qs(request.user).filter(id=project.id).exists():
        messages.error(request, "No permission for this project.")
        return redirect("accounts:chat_browse")

    target = (request.POST.get("importance") or "").strip().upper()
    allowed = {
        ChatMessage.Importance.NORMAL,
        ChatMessage.Importance.PINNED,
        ChatMessage.Importance.IGNORE,
    }
    if target not in allowed:
        messages.error(request, "Invalid importance value.")
        return redirect(reverse("accounts:chat_detail", args=[chat.id]))

    msg.importance = target
    msg.save(update_fields=["importance"])

    if target == ChatMessage.Importance.PINNED:
        rollup_segment(
            chat,
            upto_message_id=msg.id,
            user=request.user,
            trigger=ChatRollupEvent.Trigger.PIN,
        )

    next_url = (request.POST.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)

    return redirect(reverse("accounts:chat_detail", args=[chat.id]))


@require_POST
@login_required
def chat_rollup_undo(request, chat_id: int):
    chat = get_object_or_404(ChatWorkspace.objects.select_related("project"), pk=chat_id)
    if not accessible_projects_qs(request.user).filter(id=chat.project_id).exists():
        messages.error(request, "No permission for this project.")
        return redirect("accounts:chat_browse")

    res = undo_last_rollup(chat, user=request.user)
    if res.get("undone"):
        messages.success(request, "Last roll-up undone.")
    else:
        messages.info(request, "No roll-up to undo.")

    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect(reverse("accounts:chat_detail", args=[chat.id]))


@require_POST
@login_required
def chat_use_selected(request):
    chat_id = (request.POST.get("chat_id") or "").strip()
    selected_text = (request.POST.get("selected_text") or "").strip()
    apply_mode = (request.POST.get("apply_mode") or "replace").strip().lower()
    next_url = (request.POST.get("next") or "").strip()

    if apply_mode not in ("replace", "append"):
        apply_mode = "replace"

    if not chat_id or not chat_id.isdigit():
        messages.error(request, "Invalid chat.")
        return redirect("accounts:dashboard")
    if not selected_text:
        messages.error(request, "No selected text.")
        if next_url and url_has_allowed_host_and_scheme(
            url=next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)
        return redirect(reverse("accounts:chat_detail", args=[int(chat_id)]))

    chat = get_object_or_404(ChatWorkspace.objects.select_related("project"), pk=int(chat_id))
    project = chat.project

    if not accessible_projects_qs(request.user).filter(id=project.id).exists():
        messages.error(request, "No permission for this project.")
        return redirect("accounts:chat_browse")

    def merge_text(existing: str, incoming: str) -> str:
        if apply_mode == "append" and existing:
            return existing.rstrip() + "\n\n" + incoming.lstrip()
        return incoming

    def extract_json_object(text: str):
        s = (text or "").strip()
        if not s:
            return None, "Empty input."
        def _clean(raw: str) -> str:
            cleaned = raw
            cleaned = re.sub(r'">(\s*[}\]])', r'"\1', cleaned)
            cleaned = re.sub(r'>\s*(?=[}\]])', "", cleaned)
            cleaned = re.sub(r'>\s*$', "", cleaned)
            return cleaned
        s = _clean(s)
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                return obj, None
        except Exception as e:
            parse_err = str(e)
            pass
        s = _clean(s)
        try:
            decoder = json.JSONDecoder()
        except Exception:
            return None, "JSON decoder unavailable."
        idx = s.find("{")
        while idx != -1:
            try:
                obj, _end = decoder.raw_decode(s[idx:])
                if isinstance(obj, dict):
                    return obj, None
            except Exception as e:
                parse_err = str(e)
                pass
            idx = s.find("{", idx + 1)
        return None, parse_err if "parse_err" in locals() else "Invalid JSON."

    binding = ProjectTopicChat.objects.select_related("project", "user").filter(chat=chat).first()
    stage_binding = ProjectReviewStageChat.objects.filter(chat=chat, user=request.user).first()

    if not binding and stage_binding:
        payload, payload_err = extract_json_object(selected_text)
        if not isinstance(payload, dict):
            err = "Stage update requires valid JSON. Check for stray characters like '>' or missing quotes."
            if payload_err:
                err += " Parse error: " + payload_err
            messages.error(request, err)
            return redirect(reverse("accounts:chat_detail", args=[chat.id]))
        anchor, _ = ProjectAnchor.objects.get_or_create(
            project=project,
            marker=stage_binding.marker,
            defaults={"content": "", "status": ProjectAnchor.Status.DRAFT},
        )
        if stage_binding.marker == "EXECUTE":
            payload_stage_id = str(payload.get("stage_id") or "").strip()
            if not payload_stage_id:
                payload["stage_id"] = f"S{stage_binding.stage_number}"
            payload["stage_number"] = stage_binding.stage_number
            route_anchor = ProjectAnchor.objects.filter(project=project, marker="ROUTE").first()
            route_json = route_anchor.content_json if route_anchor else {}
            current_execute = anchor.content_json or {}
            proposed_execute = merge_execute_update(route_json or {}, current_execute, {"stages": [payload]})
            ok, errs = validate_execute_update(route_json or {}, current_execute, proposed_execute)
            if not ok:
                messages.error(request, "EXECUTE update rejected: " + "; ".join(errs))
                return redirect(reverse("projects:project_review", args=[project.id]) + "#review-execute")
            anchor.content_json = proposed_execute
        else:
            base = normalise_pdo_payload(anchor.content_json or {})
            stages = base.get("stages") or []
            updated = False
            payload_stage_id = str(payload.get("stage_id") or "").strip()
            for idx, item in enumerate(stages):
                try:
                    item_stage_id = str(item.get("stage_id") or "").strip()
                    if payload_stage_id and item_stage_id and payload_stage_id == item_stage_id:
                        payload["stage_number"] = int(item.get("stage_number") or stage_binding.stage_number)
                        if not payload_stage_id:
                            payload["stage_id"] = item_stage_id
                        stages[idx] = normalise_pdo_payload({"stages": [payload]}).get("stages", [payload])[0]
                        updated = True
                        break
                    if int(item.get("stage_number") or 0) == stage_binding.stage_number:
                        payload["stage_number"] = stage_binding.stage_number
                        if not payload_stage_id:
                            payload["stage_id"] = item_stage_id or f"S{stage_binding.stage_number}"
                        stages[idx] = normalise_pdo_payload({"stages": [payload]}).get("stages", [payload])[0]
                        updated = True
                        break
                except Exception:
                    continue
            if not updated:
                payload["stage_number"] = stage_binding.stage_number
                if not payload_stage_id:
                    payload["stage_id"] = f"S{stage_binding.stage_number}"
                stages.append(normalise_pdo_payload({"stages": [payload]}).get("stages", [payload])[0])
            base["stages"] = stages
            anchor.content_json = base
        anchor.content = ""
        anchor.save(update_fields=["content_json", "content", "updated_at"])
        messages.success(request, "Stage updated.")
        review_marker = "execute" if stage_binding.marker == "EXECUTE" else "route"
        return redirect(
            reverse("projects:project_review", args=[project.id])
            + "?review_chat_id="
            + str(chat.id)
            + "&review_chat_open=1&review_edit="
            + review_marker
            + "&review_anchor_open=1#review-"
            + review_marker
        )
    if not binding:
        review_binding = ProjectReviewChat.objects.filter(chat=chat, user=request.user).first()
        if not review_binding:
            messages.error(request, "This chat is not linked to a topic.")
            return redirect(reverse("accounts:chat_detail", args=[chat.id]))

        payload, payload_err = extract_json_object(selected_text)
        if review_binding.marker == "ROUTE" and not isinstance(payload, dict):
            err = "Route update requires valid JSON. Check for stray characters like '>' or missing quotes."
            if payload_err:
                err += " Parse error: " + payload_err
            messages.error(request, err)
            return redirect(
                reverse("projects:project_review", args=[project.id])
                + "?review_chat_id="
                + str(chat.id)
                + "&review_chat_open=1&review_edit=route&review_anchor_open=1#review-route"
            )
        anchor, _ = ProjectAnchor.objects.get_or_create(
            project=project,
            marker=review_binding.marker,
            defaults={"content": "", "status": ProjectAnchor.Status.DRAFT},
        )
        if payload:
            if review_binding.marker == "ROUTE":
                base = normalise_pdo_payload(anchor.content_json or {})
                incoming = normalise_pdo_payload(payload)
                merged = {
                    "pdo_summary": incoming.get("pdo_summary") or base.get("pdo_summary") or "",
                    "cko_alignment": {
                        "stage1_inputs_match": (
                            (incoming.get("cko_alignment") or {}).get("stage1_inputs_match")
                            or (base.get("cko_alignment") or {}).get("stage1_inputs_match")
                            or ""
                        ),
                        "final_outputs_match": (
                            (incoming.get("cko_alignment") or {}).get("final_outputs_match")
                            or (base.get("cko_alignment") or {}).get("final_outputs_match")
                            or ""
                        ),
                    },
                    "planning_purpose": incoming.get("planning_purpose") or base.get("planning_purpose") or "",
                    "planning_constraints": incoming.get("planning_constraints") or base.get("planning_constraints") or "",
                    "assumptions": incoming.get("assumptions") or base.get("assumptions") or "",
                    "stages": incoming.get("stages") or base.get("stages") or [],
                }
                anchor.content_json = merged
            elif review_binding.marker == "EXECUTE":
                route_anchor = ProjectAnchor.objects.filter(project=project, marker="ROUTE").first()
                route_json = route_anchor.content_json if route_anchor else {}
                current_execute = anchor.content_json or {}
                ok, errs = validate_execute_update(route_json or {}, current_execute, payload or {})
                if not ok:
                    messages.error(request, "EXECUTE update rejected: " + "; ".join(errs))
                    return redirect(reverse("projects:project_review", args=[project.id]) + "#review-execute")
                anchor.content_json = merge_execute_update(route_json or {}, current_execute, payload or {})
            else:
                anchor.content_json = payload
            anchor.content = ""
            anchor.save(update_fields=["content_json", "content", "updated_at"])
        else:
            merged = merge_text((anchor.content or "").strip(), selected_text)
            anchor.content = normalise_sections(merged)
            anchor.save(update_fields=["content", "updated_at"])
        messages.success(request, "Anchor updated.")
        marker_lower = review_binding.marker.lower()
        extra = ""
        if marker_lower == "route":
            extra = "&review_edit=route&review_anchor_open=1"
        return redirect(
            reverse("projects:project_review", args=[project.id])
            + "?review_chat_id="
            + str(chat.id)
            + "&review_chat_open=1#review-"
            + marker_lower
            + extra
        )

    if binding.user_id != request.user.id:
        messages.error(request, "No permission for this topic chat.")
        return redirect(reverse("accounts:chat_detail", args=[chat.id]))

    if binding.scope == "PPDE":
        if not can_edit_ppde(project, request.user):
            messages.error(request, "You do not have permission to edit PPDE.")
            return redirect(reverse("accounts:chat_detail", args=[chat.id]))

        if binding.topic_key == "PURPOSE":
            purpose = ProjectPlanningPurpose.objects.filter(project=project).first()
            if not purpose:
                messages.error(request, "Planning Purpose not found.")
                return redirect(reverse("accounts:chat_detail", args=[chat.id]))

            can_commit = is_project_committer(project, request.user)
            if purpose.status != ProjectPlanningPurpose.Status.DRAFT and not can_commit:
                messages.error(request, "Purpose is not editable.")
                return redirect(reverse("accounts:chat_detail", args=[chat.id]))

            if can_commit and purpose.status != ProjectPlanningPurpose.Status.DRAFT:
                purpose.status = ProjectPlanningPurpose.Status.DRAFT
                purpose.proposed_by = None
                purpose.proposed_at = None
                purpose.locked_by = None
                purpose.locked_at = None

            purpose_payload, payload_err = extract_json_object(selected_text)
            if not isinstance(purpose_payload, dict):
                err = "Purpose update requires valid JSON object."
                if payload_err:
                    err += " Parse error: " + payload_err
                messages.error(request, err)
                return redirect(reverse("accounts:chat_detail", args=[chat.id]))
            purpose_text = str(purpose_payload.get("planning_purpose") or "").strip()
            if not purpose_text:
                messages.error(request, "Missing planning_purpose.")
                return redirect(reverse("accounts:chat_detail", args=[chat.id]))

            purpose.value_text = merge_text((purpose.value_text or "").strip(), purpose_text)
            purpose.last_edited_by = request.user
            purpose.last_edited_at = timezone.now()
            purpose.save(update_fields=[
                "value_text",
                "status",
                "proposed_by",
                "proposed_at",
                "locked_by",
                "locked_at",
                "last_edited_by",
                "last_edited_at",
                "updated_at",
            ])
            messages.success(request, "Planning Purpose updated.")
            if next_url and url_has_allowed_host_and_scheme(
                url=next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + "#ppde-purpose")

        if binding.topic_key.startswith("STAGE:"):
            stage_id_raw = binding.topic_key.split(":", 1)[1].strip()
            if not stage_id_raw.isdigit():
                messages.error(request, "Invalid stage key.")
                return redirect(reverse("accounts:chat_detail", args=[chat.id]))
            stage = ProjectPlanningStage.objects.filter(project=project, id=int(stage_id_raw)).first()
            if not stage:
                messages.error(request, "Stage not found.")
                return redirect(reverse("accounts:chat_detail", args=[chat.id]))

            can_commit = is_project_committer(project, request.user)
            if stage.status != ProjectPlanningStage.Status.DRAFT and not can_commit:
                messages.error(request, "Stage is not editable.")
                return redirect(reverse("accounts:chat_detail", args=[chat.id]))

            payload, payload_err = extract_json_object(selected_text)
            if not isinstance(payload, dict):
                err = "Stage update requires valid JSON object."
                if payload_err:
                    err += " Parse error: " + payload_err
                messages.error(request, err)
                return redirect(reverse("accounts:chat_detail", args=[chat.id]))

            if can_commit and stage.status != ProjectPlanningStage.Status.DRAFT:
                stage.status = ProjectPlanningStage.Status.DRAFT
                stage.proposed_by = None
                stage.proposed_at = None
                stage.locked_by = None
                stage.locked_at = None

            def merge_field(existing_val: str, incoming_val: str) -> str:
                if apply_mode == "append" and existing_val:
                    return existing_val.rstrip() + "\n\n" + incoming_val.lstrip()
                return incoming_val

            fields = [
                "title",
                "description",
                "purpose",
                "entry_conditions",
                "acceptance_statement",
                "exit_conditions",
                "key_variables",
                "duration_estimate",
                "risks_notes",
            ]
            key_map = {
                "entry_conditions": "entry_condition",
                "exit_conditions": "exit_condition",
                "key_variables": "key_variables",
            }
            for f in fields:
                incoming = payload.get(f)
                if incoming is None:
                    continue
                incoming_s = str(incoming).strip()
                if not incoming_s:
                    continue
                dest = key_map.get(f, f)
                existing_s = (getattr(stage, dest) or "").strip()
                setattr(stage, dest, merge_field(existing_s, incoming_s))

            incoming_kd = payload.get("key_deliverables")
            if incoming_kd is not None:
                if not isinstance(incoming_kd, str):
                    messages.error(request, "key_deliverables must be a string.")
                    return redirect(reverse("accounts:chat_detail", args=[chat.id]))
                cleaned = [s.strip() for s in incoming_kd.splitlines() if s.strip()]
                if apply_mode == "append" and (stage.key_deliverables or []):
                    stage.key_deliverables = list(stage.key_deliverables or []) + cleaned
                else:
                    stage.key_deliverables = cleaned

            stage.last_edited_by = request.user
            stage.last_edited_at = timezone.now()
            stage.save(update_fields=[
                "title",
                "description",
                "purpose",
                "entry_condition",
                "acceptance_statement",
                "exit_condition",
                "key_variables",
                "key_deliverables",
                "duration_estimate",
                "risks_notes",
                "status",
                "proposed_by",
                "proposed_at",
                "locked_by",
                "locked_at",
                "last_edited_by",
                "last_edited_at",
                "updated_at",
            ])
            messages.success(request, "Stage updated.")
            if next_url and url_has_allowed_host_and_scheme(
                url=next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + "#ppde-stage-" + str(stage.id))

        if binding.topic_key.startswith("EXEC_PLAN:"):
            stage_id_raw = binding.topic_key.split(":", 1)[1].strip()
            if not stage_id_raw.isdigit():
                messages.error(request, "Invalid stage key.")
                return redirect(reverse("accounts:chat_detail", args=[chat.id]))
            stage = ProjectPlanningStage.objects.filter(project=project, id=int(stage_id_raw)).first()
            if not stage:
                messages.error(request, "Stage not found.")
                return redirect(reverse("accounts:chat_detail", args=[chat.id]))

            can_commit = is_project_committer(project, request.user)
            if stage.status != ProjectPlanningStage.Status.DRAFT and not can_commit:
                messages.error(request, "Execution plan is not editable.")
                return redirect(reverse("accounts:chat_detail", args=[chat.id]))

            payload = extract_json_object(selected_text)
            if not isinstance(payload, dict):
                messages.error(request, "Execution plan update requires valid JSON object.")
                return redirect(reverse("accounts:chat_detail", args=[chat.id]))

            milestones = payload.get("milestones") if isinstance(payload.get("milestones"), list) else []
            actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
            risks = payload.get("risks") if isinstance(payload.get("risks"), list) else []

            now = timezone.now()
            if apply_mode != "append":
                ProjectPlanningMilestone.objects.filter(project=project, stage=stage).delete()
                ProjectPlanningAction.objects.filter(project=project, stage=stage).delete()
                ProjectPlanningRisk.objects.filter(project=project, stage=stage).delete()

            m_idx = ProjectPlanningMilestone.objects.filter(project=project, stage=stage).count()
            a_idx = ProjectPlanningAction.objects.filter(project=project, stage=stage).count()
            r_idx = ProjectPlanningRisk.objects.filter(project=project, stage=stage).count()

            for item in milestones:
                if not isinstance(item, dict):
                    continue
                m_idx += 1
                ProjectPlanningMilestone.objects.create(
                    project=project,
                    stage=stage,
                    order_index=m_idx,
                    title=str(item.get("title") or ""),
                    stage_title=str(item.get("stage_title") or stage.title or ""),
                    acceptance_statement=str(item.get("acceptance_statement") or ""),
                    target_date_hint=str(item.get("target_date_hint") or ""),
                    status=ProjectPlanningMilestone.Status.PROPOSED,
                    proposed_by=request.user,
                    proposed_at=now,
                )
            for item in actions:
                if not isinstance(item, dict):
                    continue
                a_idx += 1
                ProjectPlanningAction.objects.create(
                    project=project,
                    stage=stage,
                    order_index=a_idx,
                    title=str(item.get("title") or ""),
                    stage_title=str(item.get("stage_title") or stage.title or ""),
                    owner_role=str(item.get("owner_role") or ""),
                    definition_of_done=str(item.get("definition_of_done") or ""),
                    effort_hint=str(item.get("effort_hint") or ""),
                    status=ProjectPlanningAction.Status.PROPOSED,
                    proposed_by=request.user,
                    proposed_at=now,
                )
            for item in risks:
                if not isinstance(item, dict):
                    continue
                r_idx += 1
                ProjectPlanningRisk.objects.create(
                    project=project,
                    stage=stage,
                    order_index=r_idx,
                    title=str(item.get("title") or ""),
                    stage_title=str(item.get("stage_title") or stage.title or ""),
                    probability=str(item.get("probability") or ""),
                    impact=str(item.get("impact") or ""),
                    mitigation=str(item.get("mitigation") or ""),
                    status=ProjectPlanningRisk.Status.PROPOSED,
                    proposed_by=request.user,
                    proposed_at=now,
                )

            messages.success(request, "Execution plan updated.")
            if next_url and url_has_allowed_host_and_scheme(
                url=next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + "#ppde-stage-" + str(stage.id))

    if binding.scope == "PDE" and binding.topic_key.startswith("FIELD:"):
        if not can_edit_pde(project, request.user):
            messages.error(request, "You do not have permission to edit PDE.")
            return redirect(reverse("accounts:chat_detail", args=[chat.id]))

        field_key = binding.topic_key.split(":", 1)[1].strip()
        row = ProjectDefinitionField.objects.filter(project=project, field_key=field_key).first()
        if not row:
            messages.error(request, "PDE field not found.")
            return redirect(reverse("accounts:chat_detail", args=[chat.id]))

        can_commit = is_project_committer(project, request.user)
        if row.status != ProjectDefinitionField.Status.DRAFT and not can_commit:
            messages.error(request, "Field is not editable.")
            return redirect(reverse("accounts:chat_detail", args=[chat.id]))

        if can_commit and row.status != ProjectDefinitionField.Status.DRAFT:
            row.status = ProjectDefinitionField.Status.DRAFT
            row.proposed_by = None
            row.proposed_at = None
            row.locked_by = None
            row.locked_at = None

        row.value_text = merge_text((row.value_text or "").strip(), selected_text)
        row.last_edited_by = request.user
        row.last_edited_at = timezone.now()
        row.save(update_fields=[
            "value_text",
            "status",
            "proposed_by",
            "proposed_at",
            "locked_by",
            "locked_at",
            "last_edited_by",
            "last_edited_at",
            "updated_at",
        ])
        messages.success(request, "PDE field updated.")
        return redirect(reverse("projects:pde_detail", kwargs={"project_id": project.id}) + "#pde-field-" + field_key)

    messages.error(request, "Unsupported topic type.")
    return redirect(reverse("accounts:chat_detail", args=[chat.id]))


@require_POST
@login_required
def chat_topic_delete(request, chat_id: int):
    chat = get_object_or_404(ChatWorkspace.objects.select_related("project"), pk=chat_id)

    binding = ProjectTopicChat.objects.filter(chat=chat).first()
    if not binding:
        messages.error(request, "This chat is not linked to a topic.")
        return redirect("accounts:chat_detail", chat_id=chat.id)

    if binding.user_id != request.user.id:
        messages.error(request, "No permission to delete this topic chat.")
        return redirect("accounts:chat_detail", chat_id=chat.id)

    return_url = ""
    if binding.scope == "PPDE":
        anchor = ""
        if binding.topic_key == "PURPOSE":
            anchor = "#ppde-purpose"
        elif binding.topic_key.startswith("STAGE:"):
            stage_id = binding.topic_key.split(":", 1)[1]
            if stage_id:
                anchor = "#ppde-stage-" + stage_id
        return_url = reverse("projects:ppde_detail", kwargs={"project_id": chat.project_id}) + anchor
    elif binding.scope == "PDE" and binding.topic_key.startswith("FIELD:"):
        field_key = binding.topic_key.split(":", 1)[1]
        return_url = reverse("projects:pde_detail", kwargs={"project_id": chat.project_id}) + "#pde-field-" + field_key

    chat.delete()
    messages.success(request, "Topic chat deleted.")
    if return_url:
        return redirect(return_url)
    return redirect("accounts:chat_browse")


@login_required
def chat_browse(request):
    user = request.user

    projects = (
        accessible_projects_qs(user)
        .select_related("owner", "active_l4_config")
        .order_by("name")
    )

    project_param = (request.GET.get("project") or "").strip()
    if project_param.lower() == "all":
        project_param = ""

    active_project = None
    project_filter_active = False
    pid_int = _safe_int(project_param)
    if pid_int is not None:
        active_project = projects.filter(pk=pid_int).first()
        project_filter_active = True

    if not project_filter_active:
        pid = request.session.get("rw_active_project_id")
        pid_int = _safe_int(pid)
        if pid_int is not None:
            active_project = projects.filter(pk=pid_int).first()

    if project_filter_active and active_project is not None:
        request.session["rw_active_project_id"] = active_project.id
        request.session.modified = True

    qs = (
        ChatWorkspace.objects.select_related("project", "created_by")
        .filter(project__in=projects)
        .filter(status=ChatWorkspace.Status.ACTIVE)
        .annotate(turn_count=Count("messages", filter=Q(messages__role=ChatMessage.Role.USER)))
        .annotate(attachment_count=Count("attachments", distinct=True))
    )

    if active_project is not None and not project_param:
        qs = qs.annotate(
            is_active_project=Case(
                When(project=active_project, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )

    if project_filter_active and active_project is not None:
        qs = qs.filter(project=active_project)

    status = ChatWorkspace.Status.ACTIVE
    q = (request.GET.get("q") or "").strip()

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
def dashboard_print(request):
    user = request.user

    projects = (
        accessible_projects_qs(user)
        .select_related("owner", "active_l4_config")
        .order_by("name")
    )

    active_project_id = request.session.get("rw_active_project_id")
    active_project = projects.filter(id=active_project_id).first() if active_project_id else None

    recent_projects = list(projects.order_by("-updated_at")[:25])
    recent_chats = list(
        ChatWorkspace.objects.filter(project__in=projects)
        .select_related("project")
        .order_by("-updated_at")[:25]
    )

    return render(
        request,
        "accounts/dashboard_print.html",
        {
            "active_project": active_project,
            "recent_projects": recent_projects,
            "recent_chats": recent_chats,
            "generated_at": timezone.now(),
        },
    )


@login_required
def chat_browse_print(request):
    user = request.user

    projects = (
        accessible_projects_qs(user)
        .select_related("owner", "active_l4_config")
        .order_by("name")
    )

    project_param = (request.GET.get("project") or "").strip()
    if project_param.lower() == "all":
        project_param = ""

    active_project = None
    project_filter_active = False
    pid_int = _safe_int(project_param)
    if pid_int is not None:
        active_project = projects.filter(pk=pid_int).first()
        project_filter_active = True

    if not project_filter_active:
        pid = request.session.get("rw_active_project_id")
        pid_int = _safe_int(pid)
        if pid_int is not None:
            active_project = projects.filter(pk=pid_int).first()

    qs = (
        ChatWorkspace.objects.select_related("project", "created_by")
        .filter(project__in=projects)
        .filter(status=ChatWorkspace.Status.ACTIVE)
        .annotate(turn_count=Count("messages", filter=Q(messages__role=ChatMessage.Role.USER)))
    )

    if project_filter_active and active_project is not None:
        qs = qs.filter(project=active_project)

    status = ChatWorkspace.Status.ACTIVE
    q = (request.GET.get("q") or "").strip()

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

    chats = list(qs.order_by(order_field, "-created_at"))

    return render(
        request,
        "accounts/chat_browse_print.html",
        {
            "active_project": active_project,
            "filters": {"project": project_param, "status": status, "q": q},
            "sort": sort,
            "dir": direction,
            "chats": chats,
            "generated_at": timezone.now(),
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
    chat = get_object_or_404(
        ChatWorkspace.objects.select_related("project"),
        id=chat_id,
        project__in=accessible_projects_qs(request.user),
    )
    chat_detail_path = reverse("accounts:chat_detail", args=[chat.id])
    next_url_keep_turn = request.get_full_path()

    fullscreen = request.GET.get("fullscreen") in ("1", "true", "yes")
    qs = request.GET.copy()
    qs.pop("fullscreen", None)
    qs_normal = qs.urlencode()

    qs_fs = request.GET.copy()
    qs_fs["fullscreen"] = "1"
    qs_fullscreen = qs_fs.urlencode()

    q_next = request.GET.copy()
    q_next.pop("turn", None)
    qs_next = q_next.urlencode()
    next_url_no_turn = chat_detail_path + (("?" + qs_next) if qs_next else "")

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
    active_window_turns = count_active_window_turns(chat)
    active_window_messages = count_active_window_messages(chat)
    can_undo_rollup = ChatRollupEvent.objects.filter(chat=chat, reverted_at__isnull=True).exists()

    ppde_return_url = ""
    topic_chat_return_url = ""
    binding = ProjectTopicChat.objects.filter(chat=chat).first()
    if binding and binding.scope == "PPDE":
        anchor = ""
        if binding.topic_key == "PURPOSE":
            anchor = "#ppde-purpose"
        elif binding.topic_key.startswith("STAGE:"):
            stage_id = binding.topic_key.split(":", 1)[1]
            if stage_id:
                anchor = "#ppde-stage-" + stage_id
        ppde_return_url = reverse("projects:ppde_detail", kwargs={"project_id": chat.project_id}) + anchor
        topic_chat_return_url = ppde_return_url
    elif binding and binding.scope == "PDE" and binding.topic_key.startswith("FIELD:"):
        field_key = binding.topic_key.split(":", 1)[1]
        if field_key:
            topic_chat_return_url = reverse("projects:pde_detail", kwargs={"project_id": chat.project_id}) + "#pde-field-" + field_key

    session_chat_overrides = request.session.get("rw_chat_overrides", {}) or {}
    session_per_chat = session_chat_overrides.get(str(chat.id), {}) or {}
    persisted_answer_mode = (
        str(session_per_chat.get("answer_mode") or "").strip().lower()
        or str((getattr(chat, "chat_overrides", {}) or {}).get("answer_mode") or "").strip().lower()
        or "quick"
    )
    if persisted_answer_mode not in {"quick", "full"}:
        persisted_answer_mode = "quick"

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
            "next_url_keep_turn": next_url_keep_turn,
            "next_url_no_turn": next_url_no_turn,
            "system_preview": system_preview,
            "system_latest": {},
            "has_last_image": has_last_image,
            "has_last_file": has_last_file,
            "active_window_turns": active_window_turns,
            "active_window_messages": active_window_messages,
            "pinned_cursor_message_id": chat.pinned_cursor_message_id,
            "pinned_updated_at": chat.pinned_updated_at,
            "can_undo_rollup": can_undo_rollup,
            "ppde_return_url": ppde_return_url,
            "topic_chat_return_url": topic_chat_return_url,
            "answer_mode_default": persisted_answer_mode,
            **ctx,
        },
    )


@login_required
def chat_detail_print(request, chat_id: int):
    chat = get_object_or_404(ChatWorkspace, id=chat_id)
    get_object_or_404(accessible_projects_qs(request.user), pk=chat.project_id)

    request.session["rw_active_chat_id"] = chat.id
    request.session.modified = True

    ctx = build_chat_turn_context(request, chat)
    show_system = request.GET.get("system") in ("1", "true", "yes")

    return render(
        request,
        "accounts/chat_detail_print.html",
        {
            "chat": chat,
            "turn_items": ctx.get("turn_items") or [],
            "show_system": show_system,
            "generated_at": timezone.now(),
        },
    )


# ------------------------------------------------------------
# Config menu + user/project config
# ------------------------------------------------------------

@login_required
def config_menu(request):
    profile = getattr(request.user, "profile", None)
    if profile is None:
        raise Http404("User profile not found. Run backfill / ensure profile creation.")

    if request.method == "POST":
        provider = (request.POST.get("llm_provider") or "").strip().lower()
        allowed = {"openai", "anthropic", "deepseek"}
        allowed_openai_models = {k for k, _ in ALLOWED_MODELS}
        allowed_anthropic_models = {k for k, _ in ALLOWED_ANTHROPIC_MODELS}
        allowed_deepseek_models = {k for k, _ in ALLOWED_DEEPSEEK_MODELS}

        if provider not in allowed:
            messages.error(request, "Invalid LLM provider.")
        else:
            update_fields = ["llm_provider"]
            profile.llm_provider = provider
            if provider == "openai":
                openai_model = (request.POST.get("openai_model_default") or "").strip()
                if openai_model not in allowed_openai_models:
                    messages.error(request, "Invalid OpenAI model.")
                    return redirect("accounts:config_menu")
                profile.openai_model_default = openai_model
                update_fields.append("openai_model_default")
            elif provider == "anthropic":
                anthropic_model = (request.POST.get("anthropic_model_default") or "").strip()
                if anthropic_model not in allowed_anthropic_models:
                    messages.error(request, "Invalid Anthropic model.")
                    return redirect("accounts:config_menu")
                profile.anthropic_model_default = anthropic_model
                update_fields.append("anthropic_model_default")
            elif provider == "deepseek":
                deepseek_model = (request.POST.get("deepseek_model_default") or "").strip()
                if deepseek_model not in allowed_deepseek_models:
                    messages.error(request, "Invalid DeepSeek model.")
                    return redirect("accounts:config_menu")
                profile.deepseek_model_default = deepseek_model
                update_fields.append("deepseek_model_default")
            profile.save(update_fields=update_fields)
            messages.success(request, "LLM settings updated.")
        return redirect("accounts:config_menu")

    active_chat_id = request.session.get("rw_active_chat_id")
    return render(
        request,
        "accounts/config_menu.html",
        {
            "active_chat_id": active_chat_id,
            "can_override_chat": bool(active_chat_id),
            "llm_provider": (profile.llm_provider or "openai"),
            "openai_model_default": (profile.openai_model_default or "gpt-5.1"),
            "anthropic_model_default": (
                profile.anthropic_model_default or "claude-sonnet-4-5-20250929"
            ),
            "deepseek_model_default": (profile.deepseek_model_default or "deepseek-chat"),
            "openai_model_choices": ALLOWED_MODELS,
            "anthropic_model_choices": ALLOWED_ANTHROPIC_MODELS,
            "deepseek_model_choices": ALLOWED_DEEPSEEK_MODELS,
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


def _build_rw_v2_payload(*, user_id: int, project_id: int, session_overrides: dict, chat_overrides: dict) -> dict:
    rw_v2 = {
        "tone": "Brief",
        "reasoning": "Careful",
        "approach": "Step-by-step",
        "control": "User",
    }
    try:
        effective = resolve_effective_context(
            project_id=project_id,
            user_id=user_id,
            session_overrides=session_overrides,
            chat_overrides=chat_overrides,
        )
        l4 = effective.get("level4") or {}
        rw_v2 = {
            "tone": l4.get("tone") or rw_v2["tone"],
            "reasoning": l4.get("reasoning") or rw_v2["reasoning"],
            "approach": l4.get("approach") or rw_v2["approach"],
            "control": l4.get("control") or rw_v2["control"],
        }
    except Exception:
        pass
    return rw_v2


def _push_override_update(request, chat, changed_axes: list[tuple[str, str, str]]):
    if not changed_axes:
        return

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

    if not allow_push:
        return

    chat_overrides_now = (
        request.session.get("rw_chat_overrides", {}).get(str(chat.id), {})
        or (getattr(chat, "chat_overrides", {}) or {})
    )
    session_overrides_now = request.session.get("rw_session_overrides", {}) or {}

    resolved_now = resolve_effective_context(
        project_id=chat.project_id,
        user_id=request.user.id,
        session_overrides=session_overrides_now,
        chat_overrides=chat_overrides_now,
    )

    system_blocks = build_system_messages(resolved_now)

    internal_user = "Internal: acknowledge the override is active. Say: Ready."
    panes = generate_panes(
        "\n\n".join(system_blocks) + "\n\n" + "User:\n" + internal_user,
        user=request.user,
    )

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


@login_required
def chat_config_overrides(request):
    active_chat_id = request.session.get("rw_active_chat_id")
    key = str(active_chat_id) if active_chat_id else None
    chat = (
        ChatWorkspace.objects.filter(
            pk=int(active_chat_id),
            project__in=accessible_projects_qs(request.user),
        ).first()
        if str(active_chat_id).isdigit()
        else None
    )
    chat_overrides = request.session.get("rw_chat_overrides", {}) or {}
    per_chat = {}
    if key:
        per_chat = (
            chat_overrides.get(key, {})
            or (getattr(chat, "chat_overrides", {}) if chat else {})
            or {}
        )

    if request.method == "POST" and request.POST.get("reset"):
        if key:
            chat_overrides.pop(key, None)
            request.session["rw_chat_overrides"] = chat_overrides
            if chat:
                chat.chat_overrides = {}
                chat.save(update_fields=["chat_overrides"])

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
            if chat:
                chat.chat_overrides = per_chat
                chat.save(update_fields=["chat_overrides"])
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
        if chat:
            chat.chat_overrides = per_chat
            chat.save(update_fields=["chat_overrides"])
        request.session.modified = True

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
                        chat_overrides_now = (
                            request.session.get("rw_chat_overrides", {}).get(str(chat.id), {})
                            or (getattr(chat, "chat_overrides", {}) or {})
                        )
                        session_overrides_now = request.session.get("rw_session_overrides", {}) or {}

                        resolved_now = resolve_effective_context(
                            project_id=chat.project_id,
                            user_id=request.user.id,
                            session_overrides=session_overrides_now,
                            chat_overrides=chat_overrides_now,
                        )

                        system_blocks = build_system_messages(resolved_now)

                        internal_user = "Internal: acknowledge the override is active. Say: Ready."
                        panes = generate_panes(
                            "\n\n".join(system_blocks) + "\n\n" + "User:\n" + internal_user,
                            user=request.user,
                        )

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
    chat = get_object_or_404(
        ChatWorkspace.objects.select_related("project"),
        pk=chat_id,
        project__in=accessible_projects_qs(request.user),
    )

    f = request.FILES.get("file")
    if not f:
        return JsonResponse({"ok": False, "error": "No file provided."}, status=400)
    if int(getattr(f, "size", 0) or 0) > _MAX_ATTACHMENT_BYTES:
        _record_security_event(request, "chat_attachment_too_large", size=int(getattr(f, "size", 0) or 0))
        return JsonResponse({"ok": False, "error": "Attachment is too large."}, status=400)

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

    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
    except Exception:
        payload = request.POST

    axis = str(payload.get("axis") or "").strip().lower()
    value_raw = payload.get("value")
    value = str(value_raw).strip() if value_raw is not None else ""

    allowed_avatar_axes = {
        "tone": "TONE",
        "reasoning": "REASONING",
        "approach": "APPROACH",
        "control": "CONTROL",
    }

    active_chat_id = payload.get("chat_id") or request.session.get("rw_active_chat_id")
    if not str(active_chat_id).isdigit():
        return JsonResponse({"ok": False, "error": "No active chat selected."}, status=400)

    chat = (
        ChatWorkspace.objects.filter(pk=int(active_chat_id), project__in=accessible_projects_qs(request.user))
        .select_related("project")
        .first()
    )
    if not chat:
        return JsonResponse({"ok": False, "error": "Active chat not found."}, status=404)

    chat_overrides = request.session.get("rw_chat_overrides", {}) or {}
    key = str(chat.id)
    per_chat = (
        chat_overrides.get(key, {})
        or (getattr(chat, "chat_overrides", {}) or {})
        or {}
    )

    changed_axes = []
    did_change = False

    if axis in allowed_avatar_axes:
        old_id = per_chat.get(axis)
        new_id = None
        new_name = "Default"

        if value:
            if not value.isdigit():
                return JsonResponse({"ok": False, "error": "Invalid avatar id."}, status=400)
            av = Avatar.objects.filter(
                id=int(value),
                category=allowed_avatar_axes[axis],
                is_active=True,
            ).only("id", "name").first()
            if not av:
                return JsonResponse({"ok": False, "error": "Avatar not available for axis."}, status=400)
            new_id = str(av.id)
            new_name = av.name

        if str(old_id or "") != str(new_id or ""):
            old_name = _safe_avatar_name(old_id) or "Default"
            per_chat[axis] = new_id
            changed_axes.append((axis, old_name, new_name))
            did_change = True

    elif axis == "language":
        old_language = (per_chat.get("LANGUAGE_NAME") or "").strip() or None
        new_language = value or None
        if old_language != new_language:
            per_chat["LANGUAGE_NAME"] = new_language
            changed_axes.append(("language_name", old_language or "Default", new_language or "Default"))
            did_change = True
    elif axis == "answer_mode":
        new_mode = value.lower() if value else "quick"
        if new_mode not in {"quick", "full"}:
            return JsonResponse({"ok": False, "error": "Invalid answer mode."}, status=400)
        old_mode = str(per_chat.get("answer_mode") or "quick").strip().lower()
        if old_mode not in {"quick", "full"}:
            old_mode = "quick"
        if old_mode != new_mode:
            per_chat["answer_mode"] = new_mode
            did_change = True
    else:
        return JsonResponse({"ok": False, "error": "Invalid axis."}, status=400)

    if did_change:
        chat_overrides[key] = per_chat
        request.session["rw_chat_overrides"] = chat_overrides
        chat.chat_overrides = per_chat
        chat.save(update_fields=["chat_overrides"])
        request.session.modified = True

    if changed_axes:
        try:
            _push_override_update(request, chat, changed_axes)
        except Exception as e:
            return JsonResponse(
                {
                    "ok": False,
                    "error": f"Override saved, but LLM push failed: {e}",
                },
                status=500,
            )

    session_overrides_now = request.session.get("rw_session_overrides", {}) or {}
    chat_overrides_now = (
        request.session.get("rw_chat_overrides", {}).get(str(chat.id), {})
        or (getattr(chat, "chat_overrides", {}) or {})
    )

    rw_v2 = _build_rw_v2_payload(
        user_id=request.user.id,
        project_id=chat.project_id,
        session_overrides=session_overrides_now,
        chat_overrides=chat_overrides_now,
    )

    return JsonResponse(
        {
            "ok": True,
            "changed": did_change,
            "rw_v2": rw_v2,
            "language_name": (chat_overrides_now.get("LANGUAGE_NAME") or "").strip(),
            "answer_mode": str(chat_overrides_now.get("answer_mode") or "quick"),
        }
    )



