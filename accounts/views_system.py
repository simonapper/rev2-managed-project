# -*- coding: utf-8 -*-
# accounts/views_system.py

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from config.models import ConfigRecord, ConfigVersion, SystemConfigPointers, ConfigScope


def _require_superuser(request) -> None:
    if not request.user.is_superuser:
        raise Http404()


def _get_pointers() -> SystemConfigPointers:
    obj, _ = SystemConfigPointers.objects.get_or_create(pk=1)
    return obj


def _level_label(level: int) -> str:
    try:
        return ConfigRecord.Level(level).label
    except Exception:
        return f"Level {level}"


def _pointer_field_for_level(level: int) -> str:
    mapping = {
        ConfigRecord.Level.L1: "active_l1_config",
        ConfigRecord.Level.L2: "active_l2_config",
        ConfigRecord.Level.L3: "active_l3_config",
        ConfigRecord.Level.L4: "active_l4_config",
    }
    try:
        return mapping[ConfigRecord.Level(level)]
    except Exception:
        raise Http404("Invalid level.")


@login_required
def system_settings_home(request):
    _require_superuser(request)

    pointers = _get_pointers()

    active = {
        1: pointers.active_l1_config,
        2: pointers.active_l2_config,
        3: pointers.active_l3_config,
        4: pointers.active_l4_config,
    }

    return render(
        request,
        "accounts/system/system_settings_home.html",
        {
            "active": active,
        },
    )


@login_required
def system_level_pick(request, level: int):
    """
    Select which ORG-scoped ConfigRecord is ACTIVE for a given level.
    """
    _require_superuser(request)

    # Validate level and resolve pointer field
    pointer_field = _pointer_field_for_level(level)
    level_int = int(level)

    pointers = _get_pointers()

    # Eligible records: ORG scope only, ACTIVE only, correct level
    eligible = (
        ConfigRecord.objects
        .select_related("scope")
        .filter(
            level=level_int,
            status=ConfigRecord.Status.ACTIVE,
            scope__scope_type=ConfigScope.ScopeType.ORG,
        )
        .order_by("display_name", "file_id")
    )

    if request.method == "POST":
        selected_id = request.POST.get("config_id") or ""
        if not selected_id:
            messages.error(request, "Select a config.")
            return redirect(reverse("accounts:system_settings_level_pick", args=[level_int]))

        try:
            selected_pk = int(selected_id)
        except ValueError:
            messages.error(request, "Invalid selection.")
            return redirect(reverse("accounts:system_settings_level_pick", args=[level_int]))

        selected = eligible.filter(pk=selected_pk).first()
        if not selected:
            messages.error(request, "That config is not eligible for system defaults.")
            return redirect(reverse("accounts:system_settings_level_pick", args=[level_int]))

        setattr(pointers, pointer_field, selected)
        pointers.updated_by = request.user
        pointers.save(update_fields=[pointer_field, "updated_by", "updated_at"])

        messages.success(request, f"{_level_label(level_int)} active config set.")
        return redirect("accounts:system_settings_home")

    current = getattr(pointers, pointer_field)

    return render(
        request,
        "accounts/system/system_level_pick.html",
        {
            "level": level_int,
            "level_label": _level_label(level_int),
            "eligible": eligible,
            "current": current,
        },
    )


@login_required
def system_config_detail(request, config_id: int):
    _require_superuser(request)

    cfg = get_object_or_404(
        ConfigRecord.objects.select_related("scope", "created_by"),
        pk=config_id,
    )

    # Only allow browsing ORG configs from this system surface
    if cfg.scope.scope_type != ConfigScope.ScopeType.ORG:
        raise Http404()

    versions = (
        ConfigVersion.objects
        .filter(config=cfg)
        .select_related("created_by")
        .order_by("-created_at")
    )

    latest = versions.first()

    return render(
        request,
        "accounts/system/system_config_detail.html",
        {
            "cfg": cfg,
            "versions": versions,
            "latest": latest,
        },
    )


@login_required
def system_config_version_new(request, config_id: int):
    _require_superuser(request)

    cfg = get_object_or_404(
        ConfigRecord.objects.select_related("scope"),
        pk=config_id,
    )

    if cfg.scope.scope_type != ConfigScope.ScopeType.ORG:
        raise Http404()

    if request.method == "POST":
        version = (request.POST.get("version") or "").strip() or "0.0.0"
        change_note = (request.POST.get("change_note") or "").strip()
        content_text = request.POST.get("content_text") or ""

        if not content_text.strip():
            messages.error(request, "Content cannot be empty.")
            return redirect(reverse("accounts:system_config_version_new", args=[cfg.id]))

        # Prevent duplicate (config, version)
        if ConfigVersion.objects.filter(config=cfg, version=version).exists():
            messages.error(request, "That version already exists for this config.")
            return redirect(reverse("accounts:system_config_version_new", args=[cfg.id]))

        ConfigVersion.objects.create(
            config=cfg,
            version=version,
            content_text=content_text,
            change_note=change_note,
            created_by=request.user,
        )

        messages.success(request, "New version created.")
        return redirect(reverse("accounts:system_config_detail", args=[cfg.id]))

    # Suggest next version (simple: repeat latest or default)
    latest = (
        ConfigVersion.objects
        .filter(config=cfg)
        .order_by("-created_at")
        .first()
    )
    suggested_version = latest.version if latest else "0.0.0"
    suggested_content = latest.content_text if latest else ""

    return render(
        request,
        "accounts/system/system_config_version_new.html",
        {
            "cfg": cfg,
            "suggested_version": suggested_version,
            "suggested_content": suggested_content,
        },
    )
