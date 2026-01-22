# -*- coding: utf-8 -*-
# config_ui/views_system.py

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from config.models import ConfigRecord, ConfigScope, ConfigVersion, SystemConfigPointers


def _require_superuser(request) -> None:
    if not request.user.is_superuser:
        raise Http404()


def _get_pointers() -> SystemConfigPointers:
    obj, _ = SystemConfigPointers.objects.get_or_create(pk=1)
    return obj


def _get_org_scope() -> ConfigScope:
    # ORG scope should have no project/user and empty session_id
    obj, _ = ConfigScope.objects.get_or_create(
        scope_type=ConfigScope.ScopeType.ORG,
        project=None,
        user=None,
        session_id="",
    )
    return obj


def _pointer_field_for_level(level: int) -> str:
    mapping = {
        1: "active_l1_config",
        2: "active_l2_config",
        3: "active_l3_config",
        4: "active_l4_config",
    }
    if level not in mapping:
        raise Http404("Invalid level.")
    return mapping[level]


def _level_label(level: int) -> str:
    try:
        return ConfigRecord.Level(level).label
    except Exception:
        return f"Level {level}"


def _latest_version_for(cfg: ConfigRecord) -> ConfigVersion | None:
    return (
        ConfigVersion.objects
        .filter(config=cfg)
        .order_by("-created_at")
        .first()
    )


@login_required
def system_home(request):
    _require_superuser(request)

    pointers = _get_pointers()

    active = [
        (1, pointers.active_l1_config),
        (2, pointers.active_l2_config),
        (3, pointers.active_l3_config),
        (4, pointers.active_l4_config),
    ]

    # Helpful counts per level (ORG only)
    org_scope = _get_org_scope()
    counts = {
        1: ConfigRecord.objects.filter(level=1, scope=org_scope).count(),
        2: ConfigRecord.objects.filter(level=2, scope=org_scope).count(),
        3: ConfigRecord.objects.filter(level=3, scope=org_scope).count(),
        4: ConfigRecord.objects.filter(level=4, scope=org_scope).count(),
    }

    return render(
        request,
        "config_ui/system/system_home.html",
        {
            "active": active,
            "counts": counts,
        },
    )


@login_required
def system_level_browse(request, level: int):
    _require_superuser(request)

    if level not in (1, 2, 3, 4):
        raise Http404()

    org_scope = _get_org_scope()
    pointers = _get_pointers()
    pointer_field = _pointer_field_for_level(level)
    current = getattr(pointers, pointer_field)

    configs = (
        ConfigRecord.objects
        .select_related("scope")
        .filter(level=level, scope=org_scope)
        .order_by("-created_at")
    )

    rows = []
    for c in configs:
        latest = _latest_version_for(c)
        rows.append((c, latest))

    return render(
        request,
        "config_ui/system/system_level_browse.html",
        {
            "level": level,
            "level_label": _level_label(level),
            "current": current,
            "rows": rows,
        },
    )


@login_required
def system_config_create(request, level: int):
    _require_superuser(request)

    if level not in (1, 2, 3, 4):
        raise Http404()

    org_scope = _get_org_scope()

    if request.method == "POST":
        file_id = (request.POST.get("file_id") or "").strip()
        file_name = (request.POST.get("file_name") or "").strip()
        display_name = (request.POST.get("display_name") or "").strip()
        status = (request.POST.get("status") or ConfigRecord.Status.ACTIVE).strip()
        make_active = (request.POST.get("make_active") or "") == "on"

        if not file_id or not file_name:
            messages.error(request, "file_id and file_name are required.")
            return redirect(reverse("config_ui:system_config_create", args=[level]))

        if ConfigRecord.objects.filter(level=level, file_id=file_id, scope=org_scope).exists():
            messages.error(request, "That file_id already exists for this level at ORG scope.")
            return redirect(reverse("config_ui:system_config_create", args=[level]))

        if status not in {ConfigRecord.Status.ACTIVE, ConfigRecord.Status.INACTIVE}:
            messages.error(request, "Invalid status.")
            return redirect(reverse("config_ui:system_config_create", args=[level]))

        cfg = ConfigRecord.objects.create(
            level=level,
            file_id=file_id,
            file_name=file_name,
            display_name=display_name,
            scope=org_scope,
            status=status,
            created_by=request.user,
        )

        if make_active:
            pointers = _get_pointers()
            pointer_field = _pointer_field_for_level(level)
            setattr(pointers, pointer_field, cfg)
            pointers.updated_by = request.user
            pointers.save(update_fields=[pointer_field, "updated_by", "updated_at"])
            messages.success(request, f"{_level_label(level)} config created and set active.")
        else:
            messages.success(request, f"{_level_label(level)} config created.")

        # After creating a config, go create the first version
        return redirect(reverse("config_ui:system_config_version_new", args=[cfg.id]))

    # GET: render the create form
    return render(
        request,
        "config_ui/system/system_config_create.html",
        {
            "level": level,
            "level_label": _level_label(level),
            "status_choices": ConfigRecord.Status.choices,
        },
    )


@login_required
def system_set_active(request, config_id: int):
    _require_superuser(request)

    if request.method != "POST":
        raise Http404()

    cfg = get_object_or_404(ConfigRecord.objects.select_related("scope"), pk=config_id)
    if cfg.scope.scope_type != ConfigScope.ScopeType.ORG:
        raise Http404()

    level = int(cfg.level)
    pointer_field = _pointer_field_for_level(level)

    pointers = _get_pointers()
    setattr(pointers, pointer_field, cfg)
    pointers.updated_by = request.user
    pointers.save(update_fields=[pointer_field, "updated_by", "updated_at"])

    messages.success(request, f"{_level_label(level)} active config set.")
    return redirect(reverse("config_ui:system_level_browse", args=[level]))


@login_required
def system_config_detail(request, config_id: int):
    _require_superuser(request)

    cfg = get_object_or_404(ConfigRecord.objects.select_related("scope", "created_by"), pk=config_id)
    if cfg.scope.scope_type != ConfigScope.ScopeType.ORG:
        raise Http404()

    versions = (
        ConfigVersion.objects
        .filter(config=cfg)
        .select_related("created_by")
        .order_by("-created_at")
    )
    latest = versions.first()

    pointers = _get_pointers()
    pointer_field = _pointer_field_for_level(int(cfg.level))
    active_cfg = getattr(pointers, pointer_field)
    is_active = bool(active_cfg and active_cfg.id == cfg.id)

    return render(
        request,
        "config_ui/system/system_config_detail.html",
        {
            "cfg": cfg,
            "versions": versions,
            "latest": latest,
            "is_active": is_active,
        },
    )


def _validate_l4_minimal(content_text: str) -> list[str]:
    required_markers = [
        "British English",
        "Reasoning hidden",
        "Be brief",
    ]
    return [m for m in required_markers if m.lower() not in content_text.lower()]


@login_required
def system_config_version_new(request, config_id: int):
    _require_superuser(request)

    cfg = get_object_or_404(ConfigRecord.objects.select_related("scope"), pk=config_id)
    if cfg.scope.scope_type != ConfigScope.ScopeType.ORG:
        raise Http404()

    latest = _latest_version_for(cfg)

    if request.method == "POST":
        version = (request.POST.get("version") or "").strip() or "0.0.0"
        change_note = (request.POST.get("change_note") or "").strip()
        content_text = request.POST.get("content_text") or ""

        if not content_text.strip():
            messages.error(request, "Content cannot be empty.")
            return redirect(reverse("config_ui:system_config_version_new", args=[cfg.id]))

        if ConfigVersion.objects.filter(config=cfg, version=version).exists():
            messages.error(request, "That version already exists for this config.")
            return redirect(reverse("config_ui:system_config_version_new", args=[cfg.id]))

        if int(cfg.level) == 4:
            missing = _validate_l4_minimal(content_text)
            if missing:
                messages.error(request, "L4 content is missing required markers: " + ", ".join(missing))
                return redirect(reverse("config_ui:system_config_version_new", args=[cfg.id]))

        ConfigVersion.objects.create(
            config=cfg,
            version=version,
            content_text=content_text,
            change_note=change_note,
            created_by=request.user,
        )

        messages.success(request, "New version created.")
        return redirect(reverse("config_ui:system_config_detail", args=[cfg.id]))

    suggested_version = latest.version if latest else "0.0.0"
    suggested_content = latest.content_text if latest else ""

    return render(
        request,
        "config_ui/system/system_config_version_new.html",
        {
            "cfg": cfg,
            "suggested_version": suggested_version,
            "suggested_content": suggested_content,
        },
    )
