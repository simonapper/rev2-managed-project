# -*- coding: utf-8 -*-
# projects/services/context_resolution.py

"""
Pure resolver. Reads only. Returns effective context + provenance.

Sandbox slice (Vertical Slice 1):
- Uses ORG L2 + ORG L4 from SystemConfigPointers
- Ignores L1 + L3 entirely
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from django.contrib.auth import get_user_model

from accounts.models_avatars import Avatar
from config.models import ConfigRecord, ConfigVersion, SystemConfigPointers
from projects.models import Project, UserProjectPrefs

User = get_user_model()

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

_AXIS_TO_PROFILE_FIELD = {
    "cognitive": "cognitive_avatar",
    "interaction": "interaction_avatar",
    "presentation": "presentation_avatar",
    "epistemic": "epistemic_avatar",
    "performance": "performance_avatar",
    "checkpointing": "checkpointing_avatar",
}

_CATEGORY_KEY_TO_AXIS = {
    "COGNITIVE": "cognitive",
    "INTERACTION": "interaction",
    "PRESENTATION": "presentation",
    "EPISTEMIC": "epistemic",
    "PERFORMANCE": "performance",
    "CHECKPOINTING": "checkpointing",
}


def _latest_version_for(config: ConfigRecord) -> Optional[ConfigVersion]:
    return (
        ConfigVersion.objects
        .filter(config=config)
        .order_by("-created_at", "-id")
        .first()
    )


def _config_payload(config: Optional[ConfigRecord]) -> Dict[str, Any]:
    if not config:
        return {"record": None, "version": None, "content_text": ""}
    v = _latest_version_for(config)
    return {
        "record": config,
        "version": v,
        "content_text": v.content_text if v else "",
    }


def _avatar_name_from_profile(profile: Any, axis: str) -> str:
    av = getattr(profile, _AXIS_TO_PROFILE_FIELD[axis], None)
    return av.name if av else "Default"


def _avatar_name_from_prefs(prefs: Any, axis: str) -> Optional[str]:
    av = getattr(prefs, f"{axis}_avatar", None)
    return av.name if av else None


def _apply_session_overrides_to_names(
    base_names: Dict[str, str],
    overrides: Dict[str, Any],
) -> Dict[str, str]:
    if not overrides:
        return base_names

    axis_overrides: Dict[str, Any] = {}

    for k, v in overrides.items():
        if k in _CATEGORY_KEY_TO_AXIS:
            axis_overrides[_CATEGORY_KEY_TO_AXIS[k]] = v
        elif k in _AXIS_TO_PROFILE_FIELD:
            axis_overrides[k] = v

    if not axis_overrides:
        return base_names

    out = dict(base_names)

    for axis, raw in axis_overrides.items():
        if raw in (None, "", "inherit", "INHERIT"):
            continue

        av_name = None
        try:
            av = Avatar.objects.filter(id=raw).only("name").first()
            if av:
                av_name = av.name
        except Exception:
            pass

        if av_name is None and isinstance(raw, str):
            av_name = raw.strip() or None

        if av_name:
            out[axis] = av_name

    return out


# ------------------------------------------------------------
# L4 builder (FIXED)
# ------------------------------------------------------------

def _build_level4_dict(
    *,
    user: User,
    project: Project,
    prefs: Optional[UserProjectPrefs],
    session_overrides: Dict[str, Any],
    chat_overrides: Dict[str, Any],
) -> Dict[str, Any]:

    profile = getattr(user, "profile", None)

    base_names: Dict[str, str] = {}
    for axis in _AXIS_TO_PROFILE_FIELD:
        base_names[axis] = (
            _avatar_name_from_profile(profile, axis)
            if profile else "Default"
        )

    if prefs:
        for axis in _AXIS_TO_PROFILE_FIELD:
            n = _avatar_name_from_prefs(prefs, axis)
            if n:
                base_names[axis] = n

    # IMPORTANT ORDER
    names = _apply_session_overrides_to_names(base_names, session_overrides)
    names = _apply_session_overrides_to_names(names, chat_overrides)

    return {
        "active_language_code": "en-GB",
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
    *,
    project_id: int,
    user_id: int,
    session_overrides: Optional[Dict[str, Any]] = None,
    chat_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:

    session_overrides = session_overrides or {}
    chat_overrides = chat_overrides or {}

    project = Project.objects.get(id=project_id)
    user = User.objects.get(id=user_id)

    prefs = (
        UserProjectPrefs.objects
        .filter(project_id=project_id, user_id=user_id)
        .first()
    )

    if project.kind == Project.Kind.SANDBOX:
        pointers = SystemConfigPointers.objects.get(pk=1)

        l2 = _config_payload(pointers.active_l2_config)
        l4 = _config_payload(pointers.active_l4_config)

        level4 = _build_level4_dict(
            user=user,
            project=project,
            prefs=prefs,
            session_overrides=session_overrides,
            chat_overrides=chat_overrides,
        )

        return {
            "level1": None,
            "level2": l2,
            "level3": None,
            "level4": level4,
            "provenance": {
                "project_kind": project.kind,
                "session_overrides_keys": sorted(session_overrides.keys()),
                "chat_overrides_keys": sorted(chat_overrides.keys()),
            },
        }

    raise NotImplementedError("Standard project path not yet implemented")
