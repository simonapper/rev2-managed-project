# -*- coding: utf-8 -*-
# projects/services/context_resolution.py

"""
Pure resolver. Reads only. Returns effective context + provenance.

Sandbox slice (Vertical Slice 1):
- Uses ORG L2 + ORG L4 from SystemConfigPointers
- Ignores L1 + L3 entirely

Standard projects (future slices):
- Can use project.policy active L1–L3
- Can use project.active_l4_config (project-wide L4 constraint) + user prefs overrides
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from django.contrib.auth import get_user_model
from django.db.models import QuerySet

from accounts.models_avatars import Avatar
from config.models import ConfigRecord, ConfigScope, ConfigVersion, SystemConfigPointers
from projects.models import Project, UserProjectPrefs


User = get_user_model()


# ------------------------------------------------------------
# Helpers: config version + safe lookups
# ------------------------------------------------------------

def _latest_version_for(config: ConfigRecord) -> Optional[ConfigVersion]:
    """
    Return latest ConfigVersion for a ConfigRecord.
    Uses created_at as truth (works even if version strings change).
    """
    return (
        ConfigVersion.objects.filter(config=config)
        .order_by("-created_at", "-id")
        .first()
    )


def _config_payload(config: Optional[ConfigRecord]) -> Dict[str, Any]:
    """
    Return {record, version, content_text} for a config record.
    """
    if config is None:
        return {"record": None, "version": None, "content_text": ""}

    v = _latest_version_for(config)
    return {
        "record": config,
        "version": v,
        "content_text": v.content_text if v else "",
    }


def _get_user_profile(user: User):
    return getattr(user, "profile", None)


def _safe_getattr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name)
    except Exception:
        return default


# ------------------------------------------------------------
# L4 assembly: profile defaults + project prefs + session overrides
# NOTE: build_system_messages() expects avatar NAMES, not Avatar objects.
# ------------------------------------------------------------

_AXIS_TO_PROFILE_FIELD = {
    "cognitive": "cognitive_avatar",
    "interaction": "interaction_avatar",
    "presentation": "presentation_avatar",
    "epistemic": "epistemic_avatar",
    "performance": "performance_avatar",
    "checkpointing": "checkpointing_avatar",
}

# Session override keys as used by context_processors.session_overrides_bar()
_CATEGORY_KEY_TO_AXIS = {
    "COGNITIVE": "cognitive",
    "INTERACTION": "interaction",
    "PRESENTATION": "presentation",
    "EPISTEMIC": "epistemic",
    "PERFORMANCE": "performance",
    "CHECKPOINTING": "checkpointing",
}


def _avatar_name_from_profile(profile: Any, axis: str) -> str:
    pf = _AXIS_TO_PROFILE_FIELD[axis]
    av = _safe_getattr(profile, pf, None)
    return av.name if av else "Default"


def _avatar_name_from_prefs(prefs: Any, axis: str) -> Optional[str]:
    """
    prefs.<axis>_avatar may not exist yet if migrations not applied.
    Treat missing attribute as 'inherit'.
    """
    field = f"{axis}_avatar"
    av = _safe_getattr(prefs, field, None)
    return av.name if av else None


def _apply_session_overrides_to_names(
    base_names: Dict[str, str],
    session_overrides: Dict[str, Any],
) -> Dict[str, str]:
    """
    session_overrides may contain:
    - category keys (e.g. "PERFORMANCE") -> Avatar.id (str/int) or Avatar.name
    - axis keys (e.g. "performance") -> Avatar.id (str/int) or Avatar.name
    """
    if not session_overrides:
        return base_names

    # Collect candidate overrides in axis form
    axis_overrides: Dict[str, Any] = {}

    for k, v in session_overrides.items():
        if k in _CATEGORY_KEY_TO_AXIS:
            axis_overrides[_CATEGORY_KEY_TO_AXIS[k]] = v
        elif k in _AXIS_TO_PROFILE_FIELD:
            axis_overrides[k] = v

    if not axis_overrides:
        return base_names

    # Resolve each override value
    out = dict(base_names)
    for axis, raw in axis_overrides.items():
        if raw in (None, "", "inherit", "INHERIT"):
            continue

        # If it's an id (numeric or uuid-like), try lookup
        av_name: Optional[str] = None

        # Try id lookup (common: session stores str(id))
        try:
            av = Avatar.objects.filter(id=raw).only("name").first()
            if av:
                av_name = av.name
        except Exception:
            av_name = None

        # If not id, assume it's already a name
        if av_name is None and isinstance(raw, str):
            av_name = raw.strip() or None

        if av_name:
            out[axis] = av_name

    return out


def _build_level4_dict(
    user: User,
    project: Project,
    prefs: Optional[UserProjectPrefs],
    session_overrides: Dict[str, Any],
) -> Dict[str, Any]:
    profile = _get_user_profile(user)

    # Base: user profile defaults (names)
    base_names: Dict[str, str] = {}
    for axis in _AXIS_TO_PROFILE_FIELD.keys():
        base_names[axis] = _avatar_name_from_profile(profile, axis) if profile else "Default"

    # Apply project prefs overrides (names), if present
    if prefs is not None:
        for axis in _AXIS_TO_PROFILE_FIELD.keys():
            n = _avatar_name_from_prefs(prefs, axis)
            if n:
                base_names[axis] = n

    # Apply session overrides last
    names = _apply_session_overrides_to_names(base_names, session_overrides)

    # Language
    active_language_code = "en-GB"
    if profile:
        # Your profile stores language as free text; keep code stable for now.
        # ProjectPolicy has language_default too; if you want, you can use that later.
        active_language_code = "en-GB"

    # Return shape expected by llm_instructions.build_system_messages()
    return {
        "active_language_code": active_language_code,
        "cognitive_avatar": names["cognitive"],
        "interaction_avatar": names["interaction"],
        "presentation_avatar": names["presentation"],
        "epistemic_avatar": names["epistemic"],
        "performance_avatar": names["performance"],
        "checkpointing_avatar": names["checkpointing"],
    }


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

def resolve_effective_context(
    project_id: int,
    user_id: int,
    session_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Returns:
      {
        "level1": ... (or None for Sandbox),
        "level2": ... (rules/governance text or parsed dict),
        "level3": ... (or None for Sandbox),
        "level4": dict (avatar names etc),
        "provenance": dict
      }
    """
    session_overrides = session_overrides or {}

    project = Project.objects.select_related("policy", "owner").get(id=project_id)
    user = User.objects.get(id=user_id)

    # Per-project prefs row (optional for Sandbox MVP: create lazily if missing elsewhere)
    prefs = (
        UserProjectPrefs.objects.filter(project_id=project_id, user_id=user_id)
        .select_related()  # harmless; also supports future avatar FK fields
        .first()
    )

    # --------------------------------------------------------
    # Sandbox slice path (L2 + L4 only, ORG pointers)
    # --------------------------------------------------------
    if project.kind == Project.Kind.SANDBOX:
        pointers = SystemConfigPointers.objects.select_related(
            "active_l2_config",
            "active_l4_config",
        ).get(pk=1)

        l2_payload = _config_payload(pointers.active_l2_config)
        l4_payload = _config_payload(pointers.active_l4_config)

        level4 = _build_level4_dict(user=user, project=project, prefs=prefs, session_overrides=session_overrides)

        return {
            "level1": None,
            "level2": {
                "record_id": l2_payload["record"].id if l2_payload["record"] else None,
                "version_id": l2_payload["version"].id if l2_payload["version"] else None,
                "content_text": l2_payload["content_text"],
            },
            "level3": None,
            "level4": level4,
            "provenance": {
                "project_id": project.id,
                "project_kind": project.kind,
                "l2_config_record_id": l2_payload["record"].id if l2_payload["record"] else None,
                "l2_config_version_id": l2_payload["version"].id if l2_payload["version"] else None,
                "l4_config_record_id": l4_payload["record"].id if l4_payload["record"] else None,
                "l4_config_version_id": l4_payload["version"].id if l4_payload["version"] else None,
                "user_profile_id": getattr(_get_user_profile(user), "id", None),
                "user_project_prefs_id": prefs.id if prefs else None,
                "session_overrides_keys": sorted(list(session_overrides.keys())),
                "notes": "Sandbox uses ORG pointers (L2+L4). L1/L3 ignored.",
            },
        }

    # --------------------------------------------------------
    # Standard project path (future slices)
    # --------------------------------------------------------
    policy = project.policy

    # Active project configs (Levels 1–3)
    l1_cfg = policy.active_l1_config
    l2_cfg = policy.active_l2_config
    l3_cfg = policy.active_l3_config

    l1_payload = _config_payload(l1_cfg)
    l2_payload = _config_payload(l2_cfg)
    l3_payload = _config_payload(l3_cfg)

    # L4: use project.active_l4_config as project-wide L4 constraint source if present,
    # but the SYSTEM compiler still consumes the dict from prefs/profile/overrides.
    project_l4_payload = _config_payload(project.active_l4_config) if project.active_l4_config_id else {"record": None, "version": None, "content_text": ""}

    level4 = _build_level4_dict(user=user, project=project, prefs=prefs, session_overrides=session_overrides)

    return {
        "level1": {
            "record_id": l1_payload["record"].id if l1_payload["record"] else None,
            "version_id": l1_payload["version"].id if l1_payload["version"] else None,
            "content_text": l1_payload["content_text"],
        },
        "level2": {
            "record_id": l2_payload["record"].id if l2_payload["record"] else None,
            "version_id": l2_payload["version"].id if l2_payload["version"] else None,
            "content_text": l2_payload["content_text"],
        },
        "level3": {
            "record_id": l3_payload["record"].id if l3_payload["record"] else None,
            "version_id": l3_payload["version"].id if l3_payload["version"] else None,
            "content_text": l3_payload["content_text"],
        },
        "level4": level4,
        "provenance": {
            "project_id": project.id,
            "project_kind": project.kind,
            "l1_config_record_id": l1_payload["record"].id if l1_payload["record"] else None,
            "l1_config_version_id": l1_payload["version"].id if l1_payload["version"] else None,
            "l2_config_record_id": l2_payload["record"].id if l2_payload["record"] else None,
            "l2_config_version_id": l2_payload["version"].id if l2_payload["version"] else None,
            "l3_config_record_id": l3_payload["record"].id if l3_payload["record"] else None,
            "l3_config_version_id": l3_payload["version"].id if l3_payload["version"] else None,
            "project_l4_config_record_id": project_l4_payload["record"].id if project_l4_payload["record"] else None,
            "project_l4_config_version_id": project_l4_payload["version"].id if project_l4_payload["version"] else None,
            "user_profile_id": getattr(_get_user_profile(user), "id", None),
            "user_project_prefs_id": prefs.id if prefs else None,
            "session_overrides_keys": sorted(list(session_overrides.keys())),
            "notes": "Standard path returns L1–L3 texts + L4 dict (prefs/profile/overrides).",
        },
    }
