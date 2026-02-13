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
from django.contrib.auth.models import AbstractUser


UserModel = get_user_model()

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

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


# ------------------------------------------------------------
# L4 builder (v2 only)
# ------------------------------------------------------------

def _build_level4_dict(
    *,
    user: AbstractUser,
    project: Project,
    prefs: Optional[UserProjectPrefs],
    l4_cfg: Dict[str, Any],
    session_overrides: Dict[str, Any],
    chat_overrides: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Build Level 4 effective context.
    v2 avatar axes only: tone, reasoning, approach, control.
    Precedence (low -> high):
      system/admin (l4_cfg) -> profile -> project prefs -> session -> chat
    """

    profile = getattr(user, "profile", None)

    level4: Dict[str, Any] = {}

    # 0) system/admin defaults (seeded config)
    if isinstance(l4_cfg, dict):
        level4.update(l4_cfg)

    # Always ensure language code exists
    level4.setdefault("active_language_code", "en-GB")

    # 1) user profile defaults
    if profile:
        if getattr(profile, "tone_avatar", None):
            level4["tone"] = profile.tone_avatar.name
        if getattr(profile, "reasoning_avatar", None):
            level4["reasoning"] = profile.reasoning_avatar.name
        if getattr(profile, "approach_avatar", None):
            level4["approach"] = profile.approach_avatar.name
        if getattr(profile, "control_avatar", None):
            level4["control"] = profile.control_avatar.name

    # 2) project prefs (UserProjectPrefs)
    if prefs:
        if getattr(prefs, "tone_avatar", None):
            level4["tone"] = prefs.tone_avatar.name
        if getattr(prefs, "reasoning_avatar", None):
            level4["reasoning"] = prefs.reasoning_avatar.name
        if getattr(prefs, "approach_avatar", None):
            level4["approach"] = prefs.approach_avatar.name
        if getattr(prefs, "control_avatar", None):
            level4["control"] = prefs.control_avatar.name

    def _apply_override(axis: str, raw: Any) -> None:
        if raw is None:
            return

        av_name = None

        if isinstance(raw, int):
            av = Avatar.objects.filter(id=raw).only("name").first()
            if av:
                av_name = av.name
        elif isinstance(raw, str):
            s = raw.strip()
            if s.isdigit():
                av = Avatar.objects.filter(id=int(s)).only("name").first()
                if av:
                    av_name = av.name
            elif s:
                av_name = s

        if av_name:
            level4[axis] = av_name

    # 3) session overrides (all chats this browser session)
    for axis in ("tone", "reasoning", "approach", "control"):
        _apply_override(axis, session_overrides.get(axis))

    # 4) chat overrides (highest precedence)
    for axis in ("tone", "reasoning", "approach", "control"):
        _apply_override(axis, chat_overrides.get(axis))

    return level4


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
    user = UserModel.objects.get(id=user_id)

    prefs = (
        UserProjectPrefs.objects
        .filter(project_id=project_id, user_id=user_id)
        .first()
    )

    pointers = SystemConfigPointers.objects.get(pk=1)

    l2 = _config_payload(pointers.active_l2_config)
    l4_source = project.active_l4_config or pointers.active_l4_config
    l4_cfg = _config_payload(l4_source)

    level4 = _build_level4_dict(
        user=user,
        project=project,
        prefs=prefs,
        l4_cfg=l4_cfg,
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
            "system_l4_config_id": getattr(pointers.active_l4_config, "id", None),
            "project_l4_config_id": getattr(project.active_l4_config, "id", None),
            "session_overrides_keys": sorted(session_overrides.keys()),
            "chat_overrides_keys": sorted(chat_overrides.keys()),
        },
    }
