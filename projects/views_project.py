# -*- coding: utf-8 -*-
# projects/views_project.py

from __future__ import annotations

from django import forms
import io
import json
import logging
import os
import re
import zipfile
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import Coalesce
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.decorators.http import require_POST

from chats.models import ChatMessage, ChatWorkspace
from chats.services_boundaries import resolve_boundary_profile
from chats.services.chat_bootstrap import bootstrap_chat
from chats.services.cleanup import delete_empty_sandbox_chats
from chats.services.llm import generate_panes
from config.models import ConfigRecord, ConfigScope, ConfigVersion
from projects.models import (
    Project,
    ProjectAnchor,
    ProjectCKO,
    ProjectMembership,
    ProjectPDO,
    ProjectPKO,
    ProjectTKO,
    ProjectWKO,
    PolicyDocument,
)
from projects.services.project_bootstrap import bootstrap_project
from projects.services_project_membership import accessible_projects_qs, is_project_manager, can_edit_committee
from uploads.models import ChatAttachment, GeneratedImage

_MAX_IMPORT_ZIP_BYTES = 50 * 1024 * 1024
_MAX_IMPORT_FILES = 2000
_MAX_IMPORT_MEMBER_BYTES = 25 * 1024 * 1024
_MAX_IMPORT_TOTAL_BYTES = 300 * 1024 * 1024
_MAX_IMPORT_RATIO = 200
_IMPORT_RATE_LIMIT_WINDOW_SECONDS = 60
_IMPORT_RATE_LIMIT_MAX = 6

_SECURITY_LOG = logging.getLogger("workbench.security")


def _boundary_profile_from_post(post_data) -> dict:
    topic_tags_raw = (post_data.get("boundary_topic_tags") or "").strip()
    topic_tags = [v.strip().upper() for v in topic_tags_raw.replace(";", ",").split(",") if v.strip()]
    jurisdiction = "UK" if any(tag.startswith("UK_") or tag == "UK" for tag in topic_tags) else "NONE"

    return {
        "strictness": "SOFT",
        "jurisdiction": jurisdiction,
        "topic_tags": topic_tags,
        "authority_set": {
            "allow_model_general_knowledge": bool(post_data.get("boundary_allow_general")),
            "allow_internal_docs": bool(post_data.get("boundary_allow_internal_docs")),
            "allow_public_sources": False,
        },
        "out_of_scope_behaviour": "ALLOW_WITH_WARNING",
        "recency_risk_topics": ["TAX_RATES", "THRESHOLDS", "DEADLINES"],
        "required_labels": {
            "scope_flag": True,
            "assumptions": True,
            "source_basis": True,
            "confidence": True,
        },
    }


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


def _delete_project_permanently(project: Project) -> None:
    with transaction.atomic():
        Project.objects.filter(pk=project.pk).update(active_l4_config=None)

        scopes = ConfigScope.objects.filter(project=project)
        ConfigVersion.objects.filter(config__scope__in=scopes).delete()
        ConfigRecord.objects.filter(scope__in=scopes).delete()
        scopes.delete()

        project.delete()


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


def _safe_next_url(request, fallback: str) -> str:
    next_url = (request.POST.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    if next_url:
        _record_security_event(request, "blocked_next_redirect", next_url=next_url)
    return fallback


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


@login_required
def active_project_set(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)

    project_id = request.POST.get("project_id")
    if not project_id:
        messages.error(request, "No project selected.")
        return redirect(_safe_next_url(request, reverse("accounts:dashboard")))

    try:
        pid = int(project_id)
    except ValueError:
        messages.error(request, "Invalid project.")
        return redirect(_safe_next_url(request, reverse("accounts:dashboard")))

    active_project = get_object_or_404(accessible_projects_qs(request.user), pk=pid)

    request.session["rw_active_project_id"] = active_project.id
    request.session.modified = True

    return redirect(_safe_next_url(request, reverse("accounts:dashboard")))


# ------------------------------------------------------------
# Projects (home/create/delete/select/project_chat_list)
# ------------------------------------------------------------

@login_required
def project_home(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), id=project_id)

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
                chat, _ = bootstrap_chat(
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

    if not (request.user.is_superuser or p.owner_id == request.user.id):
        messages.error(request, "You do not have permission to delete this project.")
        return redirect("accounts:project_config_list")

    name = p.name or "(unnamed project)"

    _delete_project_permanently(p)

    if str(request.session.get("rw_active_project_id")) == str(project_id):
        request.session.pop("rw_active_project_id", None)
        request.session.pop("rw_active_chat_id", None)
        request.session.modified = True

    messages.success(request, f"Project deleted permanently: {name}")
    return redirect("accounts:project_config_list")


@require_POST
@login_required
def project_archive(request, project_id: int):
    p = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    if not (request.user.is_superuser or p.owner_id == request.user.id):
        messages.error(request, "You do not have permission to archive this project.")
        return redirect("accounts:project_config_list")

    if p.status != Project.Status.ARCHIVED:
        p.status = Project.Status.ARCHIVED
        p.save(update_fields=["status", "updated_at"])
        ChatWorkspace.objects.filter(project=p).exclude(status=ChatWorkspace.Status.ARCHIVED).update(
            status=ChatWorkspace.Status.ARCHIVED
        )

    if str(request.session.get("rw_active_project_id")) == str(project_id):
        request.session.pop("rw_active_project_id", None)
        request.session.pop("rw_active_chat_id", None)
        request.session.modified = True

    messages.success(request, "Project archived (all chats archived).")
    return redirect("accounts:project_config_list")


def _safe_zip_name(name: str) -> str:
    s = (name or "item").strip()
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    return s[:80] or "item"


def _unique_project_name(base: str) -> str:
    name = (base or "Imported Project").strip()[:200] or "Imported Project"
    if not Project.objects.filter(name=name).exists():
        return name
    i = 2
    while True:
        candidate = (f"{name} ({i})")[:200]
        if not Project.objects.filter(name=candidate).exists():
            return candidate
        i += 1


@require_POST
@login_required
def project_export(request, project_id: int):
    p = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    if not (request.user.is_superuser or p.owner_id == request.user.id):
        messages.error(request, "You do not have permission to export this project.")
        return redirect("accounts:project_config_list")

    chats = list(ChatWorkspace.objects.filter(project=p).order_by("id"))
    payload = {
        "type": "project_export_v1",
        "project": {
            "name": p.name,
            "description": p.description or "",
            "purpose": p.purpose or "",
            "kind": p.kind,
            "primary_type": p.primary_type,
            "mode": p.mode,
            "status": p.status,
            "defined_cko_version": (p.defined_cko.version if p.defined_cko_id else None),
        },
        "policy_documents": [],
        "anchors": [],
        "cko_versions": [],
        "tko_versions": [],
        "wko_versions": [],
        "pko_versions": [],
        "pdo_versions": [],
        "chats": [],
    }

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for a in ProjectAnchor.objects.filter(project=p).order_by("marker", "id"):
            payload["anchors"].append(
                {
                    "marker": a.marker,
                    "content": a.content or "",
                    "content_json": a.content_json or {},
                    "status": a.status,
                }
            )

        for row in ProjectCKO.objects.filter(project=p).order_by("version", "id"):
            payload["cko_versions"].append(
                {
                    "version": row.version,
                    "status": row.status,
                    "rel_path": row.rel_path or "",
                    "content_html": row.content_html or "",
                    "content_text": row.content_text or "",
                    "content_json": row.content_json or {},
                    "field_snapshot": row.field_snapshot or {},
                }
            )

        for row in ProjectTKO.objects.filter(project=p).order_by("version", "id"):
            payload["tko_versions"].append(
                {
                    "version": row.version,
                    "status": row.status,
                    "content_text": row.content_text or "",
                    "content_json": row.content_json or {},
                    "content_html": row.content_html or "",
                }
            )

        for row in ProjectWKO.objects.filter(project=p).order_by("version", "id"):
            payload["wko_versions"].append(
                {
                    "version": row.version,
                    "status": row.status,
                    "structure_contract_key": row.structure_contract_key or "",
                    "structure_contract_version": row.structure_contract_version,
                    "transform_contract_key": row.transform_contract_key or "",
                    "transform_contract_version": row.transform_contract_version,
                    "content_json": row.content_json or {},
                    "seed_snapshot": row.seed_snapshot or {},
                    "change_summary": row.change_summary or "",
                }
            )

        for row in ProjectPKO.objects.filter(project=p).order_by("version", "id"):
            payload["pko_versions"].append(
                {
                    "version": row.version,
                    "status": row.status,
                    "content_text": row.content_text or "",
                    "content_json": row.content_json or {},
                    "content_html": row.content_html or "",
                }
            )

        for row in ProjectPDO.objects.filter(project=p).order_by("version", "id"):
            payload["pdo_versions"].append(
                {
                    "version": row.version,
                    "status": row.status,
                    "content_json": row.content_json or {},
                    "seed_snapshot": row.seed_snapshot or {},
                    "change_summary": row.change_summary or "",
                }
            )

        for doc in PolicyDocument.objects.filter(project=p).order_by("id"):
            payload["policy_documents"].append(
                {
                    "title": doc.title or "",
                    "body_text": doc.body_text or "",
                    "source_ref": doc.source_ref or "",
                    "updated_at": doc.updated_at.isoformat() if doc.updated_at else "",
                }
            )

        for chat in chats:
            chat_payload = {
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
                "messages": [],
                "attachments": [],
                "generated_images": [],
            }
            for m in ChatMessage.objects.filter(chat=chat).order_by("id"):
                chat_payload["messages"].append(
                    {
                        "id": m.id,
                        "role": m.role,
                        "importance": m.importance,
                        "raw_text": m.raw_text or "",
                        "answer_text": m.answer_text or "",
                        "reasoning_text": m.reasoning_text or "",
                        "output_text": m.output_text or "",
                        "segment_meta": m.segment_meta or {},
                    }
                )

            for a in ChatAttachment.objects.filter(chat=chat).order_by("id"):
                base = _safe_zip_name(a.original_name or f"attachment_{a.id}")
                arc = f"attachments/chat_{chat.id}/{a.id}_{base}"
                try:
                    with a.file.open("rb") as fh:
                        zf.writestr(arc, fh.read())
                except Exception:
                    continue
                chat_payload["attachments"].append(
                    {
                        "path": arc,
                        "original_name": a.original_name or "",
                        "content_type": a.content_type or "",
                        "size_bytes": int(a.size_bytes or 0),
                    }
                )
            for gi in GeneratedImage.objects.filter(chat=chat).order_by("id"):
                ext = (str(gi.image_file.name or "").rsplit(".", 1)[-1] if gi.image_file else "png").lower()
                arc = f"generated_images/chat_{chat.id}/{gi.id}_{(gi.sha256 or 'img')[:16]}.{ext}"
                try:
                    with gi.image_file.open("rb") as fh:
                        zf.writestr(arc, fh.read())
                except Exception:
                    continue
                chat_payload["generated_images"].append(
                    {
                        "path": arc,
                        "message_id": gi.message_id,
                        "provider": gi.provider or "",
                        "model": gi.model or "",
                        "prompt": gi.prompt or "",
                        "file_id": gi.file_id or "",
                        "mime_type": gi.mime_type or "image/png",
                        "width": gi.width,
                        "height": gi.height,
                        "sha256": gi.sha256 or "",
                    }
                )

            payload["chats"].append(chat_payload)

        zf.writestr("project.json", json.dumps(payload, ensure_ascii=True, indent=2))

    project_name = p.name
    project_id_str = str(p.id)

    filename = _safe_zip_name(project_name or f"project_{project_id_str}") + ".zip"
    resp = HttpResponse(zip_buffer.getvalue(), content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@require_POST
@login_required
def project_import(request):
    if not _check_import_rate_limit(user_id=request.user.id, scope="project"):
        _record_security_event(request, "project_import_rate_limited")
        messages.error(request, "Too many import attempts. Please wait a minute and try again.")
        return redirect("accounts:project_config_list")

    f = request.FILES.get("project_file")
    if not f:
        messages.error(request, "Choose a project export ZIP to import.")
        return redirect("accounts:project_config_list")
    if int(getattr(f, "size", 0) or 0) > _MAX_IMPORT_ZIP_BYTES:
        _record_security_event(request, "project_import_zip_too_large", size=int(getattr(f, "size", 0) or 0))
        messages.error(request, "Project import ZIP is too large.")
        return redirect("accounts:project_config_list")

    try:
        with zipfile.ZipFile(f) as zf:
            _validate_import_zip_safety(zf)
            payload = json.loads(_safe_zip_read(zf, "project.json", max_bytes=_MAX_IMPORT_MEMBER_BYTES).decode("utf-8"))

            if payload.get("type") != "project_export_v1":
                messages.error(request, "Unsupported project export format.")
                return redirect("accounts:project_config_list")

            proj = payload.get("project") or {}
            project = Project.objects.create(
                name=_unique_project_name(str(proj.get("name") or "Imported Project")),
                description=str(proj.get("description") or ""),
                purpose=str(proj.get("purpose") or ""),
                kind=str(proj.get("kind") or Project.Kind.STANDARD),
                primary_type=str(proj.get("primary_type") or Project.PrimaryType.DELIVERY),
                mode=str(proj.get("mode") or Project.Mode.PLAN),
                status=Project.Status.ACTIVE,
                owner=request.user,
            )
            bootstrap_project(project=project)

            valid_anchor_markers = {k for k, _ in ProjectAnchor.Marker.choices}
            valid_anchor_statuses = {k for k, _ in ProjectAnchor.Status.choices}
            for anchor_payload in payload.get("anchors") or []:
                if not isinstance(anchor_payload, dict):
                    continue
                marker = str(anchor_payload.get("marker") or "").strip().upper()
                if marker not in valid_anchor_markers:
                    continue
                status = str(anchor_payload.get("status") or ProjectAnchor.Status.DRAFT).strip().upper()
                if status not in valid_anchor_statuses:
                    status = ProjectAnchor.Status.DRAFT
                ProjectAnchor.objects.update_or_create(
                    project=project,
                    marker=marker,
                    defaults={
                        "content": str(anchor_payload.get("content") or ""),
                        "content_json": anchor_payload.get("content_json") if isinstance(anchor_payload.get("content_json"), dict) else {},
                        "status": status,
                        "last_edited_by": request.user,
                        "last_edited_at": timezone.now(),
                    },
                )

            valid_cko_statuses = {k for k, _ in ProjectCKO.Status.choices}
            created_cko_by_version = {}
            for cko_payload in payload.get("cko_versions") or []:
                if not isinstance(cko_payload, dict):
                    continue
                version = cko_payload.get("version")
                if not isinstance(version, int):
                    continue
                status = str(cko_payload.get("status") or ProjectCKO.Status.DRAFT).strip().upper()
                if status not in valid_cko_statuses:
                    status = ProjectCKO.Status.DRAFT
                row = ProjectCKO.objects.create(
                    project=project,
                    version=version,
                    status=status,
                    rel_path=str(cko_payload.get("rel_path") or ""),
                    content_html=str(cko_payload.get("content_html") or ""),
                    content_text=str(cko_payload.get("content_text") or ""),
                    content_json=cko_payload.get("content_json") if isinstance(cko_payload.get("content_json"), dict) else {},
                    field_snapshot=cko_payload.get("field_snapshot") if isinstance(cko_payload.get("field_snapshot"), dict) else {},
                    created_by=request.user,
                    accepted_by=request.user if status == ProjectCKO.Status.ACCEPTED else None,
                    accepted_at=timezone.now() if status == ProjectCKO.Status.ACCEPTED else None,
                )
                created_cko_by_version[version] = row

            defined_cko_version = (payload.get("project") or {}).get("defined_cko_version")
            if isinstance(defined_cko_version, int):
                defined_cko = created_cko_by_version.get(defined_cko_version)
                if defined_cko:
                    project.defined_cko = defined_cko
                    project.defined_at = timezone.now()
                    project.defined_by = request.user
                    project.save(update_fields=["defined_cko", "defined_at", "defined_by", "updated_at"])

            valid_tko_statuses = {k for k, _ in ProjectTKO.Status.choices}
            for tko_payload in payload.get("tko_versions") or []:
                if not isinstance(tko_payload, dict):
                    continue
                version = tko_payload.get("version")
                if not isinstance(version, int):
                    continue
                status = str(tko_payload.get("status") or ProjectTKO.Status.DRAFT).strip().upper()
                if status not in valid_tko_statuses:
                    status = ProjectTKO.Status.DRAFT
                ProjectTKO.objects.create(
                    project=project,
                    version=version,
                    status=status,
                    content_text=str(tko_payload.get("content_text") or ""),
                    content_json=tko_payload.get("content_json") if isinstance(tko_payload.get("content_json"), dict) else {},
                    content_html=str(tko_payload.get("content_html") or ""),
                    created_by=request.user,
                    accepted_by=request.user if status == ProjectTKO.Status.ACCEPTED else None,
                    accepted_at=timezone.now() if status == ProjectTKO.Status.ACCEPTED else None,
                )

            valid_wko_statuses = {k for k, _ in ProjectWKO.Status.choices}
            for wko_payload in payload.get("wko_versions") or []:
                if not isinstance(wko_payload, dict):
                    continue
                version = wko_payload.get("version")
                if not isinstance(version, int):
                    continue
                status = str(wko_payload.get("status") or ProjectWKO.Status.DRAFT).strip().upper()
                if status not in valid_wko_statuses:
                    status = ProjectWKO.Status.DRAFT
                ProjectWKO.objects.create(
                    project=project,
                    version=version,
                    status=status,
                    structure_contract_key=str(wko_payload.get("structure_contract_key") or ""),
                    structure_contract_version=(
                        wko_payload.get("structure_contract_version")
                        if isinstance(wko_payload.get("structure_contract_version"), int)
                        else None
                    ),
                    transform_contract_key=str(wko_payload.get("transform_contract_key") or ""),
                    transform_contract_version=(
                        wko_payload.get("transform_contract_version")
                        if isinstance(wko_payload.get("transform_contract_version"), int)
                        else None
                    ),
                    content_json=wko_payload.get("content_json") if isinstance(wko_payload.get("content_json"), dict) else {},
                    seed_snapshot=wko_payload.get("seed_snapshot") if isinstance(wko_payload.get("seed_snapshot"), dict) else {},
                    change_summary=str(wko_payload.get("change_summary") or ""),
                    created_by=request.user,
                    activated_by=request.user if status == ProjectWKO.Status.ACTIVE else None,
                    activated_at=timezone.now() if status == ProjectWKO.Status.ACTIVE else None,
                )

            valid_pko_statuses = {k for k, _ in ProjectPKO.Status.choices}
            for pko_payload in payload.get("pko_versions") or []:
                if not isinstance(pko_payload, dict):
                    continue
                version = pko_payload.get("version")
                if not isinstance(version, int):
                    continue
                status = str(pko_payload.get("status") or ProjectPKO.Status.DRAFT).strip().upper()
                if status not in valid_pko_statuses:
                    status = ProjectPKO.Status.DRAFT
                ProjectPKO.objects.create(
                    project=project,
                    version=version,
                    status=status,
                    content_text=str(pko_payload.get("content_text") or ""),
                    content_json=pko_payload.get("content_json") if isinstance(pko_payload.get("content_json"), dict) else {},
                    content_html=str(pko_payload.get("content_html") or ""),
                    created_by=request.user,
                    accepted_by=request.user if status == ProjectPKO.Status.ACCEPTED else None,
                    accepted_at=timezone.now() if status == ProjectPKO.Status.ACCEPTED else None,
                )

            valid_pdo_statuses = {k for k, _ in ProjectPDO.Status.choices}
            for pdo_payload in payload.get("pdo_versions") or []:
                if not isinstance(pdo_payload, dict):
                    continue
                version = pdo_payload.get("version")
                if not isinstance(version, int):
                    continue
                status = str(pdo_payload.get("status") or ProjectPDO.Status.DRAFT).strip().upper()
                if status not in valid_pdo_statuses:
                    status = ProjectPDO.Status.DRAFT
                ProjectPDO.objects.create(
                    project=project,
                    version=version,
                    status=status,
                    content_json=pdo_payload.get("content_json") if isinstance(pdo_payload.get("content_json"), dict) else {},
                    seed_snapshot=pdo_payload.get("seed_snapshot") if isinstance(pdo_payload.get("seed_snapshot"), dict) else {},
                    change_summary=str(pdo_payload.get("change_summary") or ""),
                    created_by=request.user,
                )

            for doc_payload in payload.get("policy_documents") or []:
                if not isinstance(doc_payload, dict):
                    continue
                title = str(doc_payload.get("title") or "").strip()[:200]
                if not title:
                    continue
                PolicyDocument.objects.create(
                    project=project,
                    title=title,
                    body_text=str(doc_payload.get("body_text") or ""),
                    source_ref=str(doc_payload.get("source_ref") or "")[:255],
                )

            for chat_payload in payload.get("chats") or []:
                if not isinstance(chat_payload, dict):
                    continue
                chat = ChatWorkspace.objects.create(
                    project=project,
                    title=str(chat_payload.get("title") or "Imported chat")[:250],
                    status=ChatWorkspace.Status.ACTIVE,
                    created_by=request.user,
                    goal_text=str(chat_payload.get("goal_text") or ""),
                    success_text=str(chat_payload.get("success_text") or ""),
                    constraints_text=str(chat_payload.get("constraints_text") or ""),
                    non_goals_text=str(chat_payload.get("non_goals_text") or ""),
                    cde_is_locked=bool(chat_payload.get("cde_is_locked")),
                    cde_json=chat_payload.get("cde_json") if isinstance(chat_payload.get("cde_json"), dict) else {},
                    chat_overrides=chat_payload.get("chat_overrides") if isinstance(chat_payload.get("chat_overrides"), dict) else {},
                    pinned_summary=str(chat_payload.get("pinned_summary") or ""),
                    pinned_conclusion=str(chat_payload.get("pinned_conclusion") or ""),
                    pinned_cursor_message_id=chat_payload.get("pinned_cursor_message_id"),
                    pinned_updated_at=timezone.now(),
                )

                old_to_new_message = {}
                for m in chat_payload.get("messages") or []:
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
                    saved_msg = ChatMessage.objects.create(
                        chat=chat,
                        role=role,
                        importance=importance,
                        raw_text=str(m.get("raw_text") or ""),
                        answer_text=str(m.get("answer_text") or ""),
                        reasoning_text=str(m.get("reasoning_text") or ""),
                        output_text=str(m.get("output_text") or ""),
                        segment_meta=m.get("segment_meta") if isinstance(m.get("segment_meta"), dict) else {},
                    )
                    src_id = m.get("id")
                    if isinstance(src_id, int):
                        old_to_new_message[src_id] = saved_msg.id

                for a in chat_payload.get("attachments") or []:
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
                for gi in chat_payload.get("generated_images") or []:
                    if not isinstance(gi, dict):
                        continue
                    arc_path = str(gi.get("path") or "")
                    if not arc_path:
                        continue
                    try:
                        blob = _safe_zip_read(zf, arc_path, max_bytes=_MAX_IMPORT_MEMBER_BYTES)
                    except Exception:
                        continue
                    ext = "." + (arc_path.rsplit(".", 1)[-1].lower() if "." in arc_path else "png")
                    name = (str(gi.get("sha256") or "")[:64] or "generated") + ext
                    src_message_id = gi.get("message_id")
                    new_message_id = old_to_new_message.get(src_message_id) if isinstance(src_message_id, int) else None
                    GeneratedImage.objects.create(
                        project=project,
                        chat=chat,
                        message_id=new_message_id,
                        provider=str(gi.get("provider") or ""),
                        model=str(gi.get("model") or ""),
                        prompt=str(gi.get("prompt") or ""),
                        file_id=str(gi.get("file_id") or ""),
                        mime_type=str(gi.get("mime_type") or "image/png"),
                        width=gi.get("width") if isinstance(gi.get("width"), int) else None,
                        height=gi.get("height") if isinstance(gi.get("height"), int) else None,
                        sha256=str(gi.get("sha256") or ""),
                        image_file=ContentFile(blob, name=name),
                    )
    except Exception as exc:
        _record_security_event(request, "project_import_invalid_zip", error=str(exc)[:160])
        messages.error(request, f"Invalid project export: {exc}")
        return redirect("accounts:project_config_list")

    request.session["rw_active_project_id"] = project.id
    request.session.pop("rw_active_chat_id", None)
    request.session.modified = True
    messages.success(request, "Project imported.")
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

    qs = ChatWorkspace.objects.select_related("created_by").filter(
        project=active_project,
        status=ChatWorkspace.Status.ACTIVE,
    )

    status = ChatWorkspace.Status.ACTIVE
    q = (request.GET.get("q") or "").strip()

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
def project_browse_print(request):
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

    projects = list(
        qs.select_related("owner", "active_l4_config").order_by(order_field, "name")
    )
    projects_with_permissions = [(proj, is_project_manager(proj, user)) for proj in projects]

    return render(
        request,
        "accounts/project_browse_print.html",
        {
            "projects_with_permissions": projects_with_permissions,
            "sort": sort,
            "dir": direction,
            "generated_at": timezone.now(),
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
    boundary_profile = resolve_boundary_profile(active_project, None)
    policy_documents = list(
        PolicyDocument.objects.filter(project=active_project).order_by("-updated_at", "-id")
    )

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
    planning_mode = ProjectMembership.PlanningMode.ASSISTED
    my_membership = (
        ProjectMembership.objects.filter(
            project=active_project,
            user=request.user,
            status=ProjectMembership.Status.ACTIVE,
            effective_to__isnull=True,
        )
        .order_by("id")
        .first()
    )
    if my_membership and (my_membership.planning_mode in dict(ProjectMembership.PlanningMode.choices)):
        planning_mode = my_membership.planning_mode

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

    if request.method == "POST" and (request.POST.get("action") or "") == "policy_doc_create":
        title = (request.POST.get("doc_title") or "").strip()
        body_text = (request.POST.get("doc_body_text") or "").strip()
        source_ref = (request.POST.get("doc_source_ref") or "").strip()
        if not title or not body_text:
            messages.error(request, "Policy document title and text are required.")
        else:
            PolicyDocument.objects.create(
                project=active_project,
                title=title[:200],
                body_text=body_text,
                source_ref=source_ref[:255],
            )
            messages.success(request, "Policy document saved.")
        return redirect("accounts:project_config_info", project_id=active_project.id)

    if request.method == "POST" and (request.POST.get("action") or "") == "policy_doc_delete":
        doc_id_raw = (request.POST.get("doc_id") or "").strip()
        if doc_id_raw.isdigit():
            PolicyDocument.objects.filter(project=active_project, id=int(doc_id_raw)).delete()
            messages.success(request, "Policy document deleted.")
        else:
            messages.error(request, "Invalid policy document.")
        return redirect("accounts:project_config_info", project_id=active_project.id)

    if request.method == "POST" and (request.POST.get("action") or "") == "boundary_update":
        active_project.boundary_profile_json = _boundary_profile_from_post(request.POST)
        active_project.save(update_fields=["boundary_profile_json", "updated_at"])
        messages.success(request, "Project boundaries updated.")
        return redirect("accounts:project_config_info", project_id=active_project.id)

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
            "planning_mode": planning_mode,
            "boundary_profile": boundary_profile,
            "policy_documents": policy_documents,
            "policy_docs_help_url": reverse("projects:policy_docs_help", args=[active_project.id]),
            "member_rows": member_rows,
            "available_users": available_users,
            "can_edit_committee": can_edit_team,
        },
    )


@login_required
def policy_docs_help(request, project_id: int):
    active_project = get_object_or_404(
        accessible_projects_qs(request.user),
        pk=project_id,
    )
    return render(
        request,
        "accounts/policy_docs_help.html",
        {
            "project": active_project,
        },
    )


@require_POST
@login_required
def set_planning_mode(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)

    mode = (request.POST.get("mode") or "").strip().upper()
    allowed_modes = {
        ProjectMembership.PlanningMode.ASSISTED,
        ProjectMembership.PlanningMode.AUTO,
    }
    if mode not in allowed_modes:
        messages.error(request, "Invalid planning mode.")
    else:
        membership = (
            ProjectMembership.objects.filter(
                project=project,
                user=request.user,
                status=ProjectMembership.Status.ACTIVE,
                effective_to__isnull=True,
            )
            .order_by("id")
            .first()
        )
        if membership is None and request.user.id == project.owner_id:
            membership = ProjectMembership.objects.create(
                project=project,
                user=request.user,
                role=ProjectMembership.Role.OWNER,
                scope_type=ProjectMembership.ScopeType.PROJECT,
                scope_ref="",
                status=ProjectMembership.Status.ACTIVE,
                planning_mode=mode,
            )
        elif membership is not None:
            membership.planning_mode = mode
            membership.save(update_fields=["planning_mode", "updated_at"])

        messages.success(request, "Planning mode updated.")

    next_url = (request.POST.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect("accounts:project_config_info", project_id=project.id)


@login_required
def project_detail_print(request, project_id: int):
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

    return render(
        request,
        "accounts/project_detail_print.html",
        {
            "project": active_project,
            "accepted_cko": accepted_cko,
            "cko_history": cko_history,
            "active_wko": latest_wko,
            "member_rows": member_rows,
            "generated_at": timezone.now(),
        },
    )


@login_required
def project_config_definitions(request, project_id: int):
    active_project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    return render(request, "accounts/config_project_definitions.html", {"project": active_project})


@login_required
def project_config_edit(request, project_id):
    project = get_object_or_404(accessible_projects_qs(request.user), id=project_id)

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
