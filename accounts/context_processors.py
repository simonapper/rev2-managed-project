# -*- coding: utf-8 -*-
# accounts/context_processors.py

from __future__ import annotations

from typing import Any, Dict, Optional

from django.contrib.auth.models import AnonymousUser
from django.db.models import Q

from accounts.models_avatars import Avatar
from projects.models import Project, UserProjectPrefs
from projects.services_project_membership import accessible_projects_qs
from chats.models import ChatMessage
from chats.models import ChatWorkspace


def topbar_context(request):
    ctx = {}

    chat_id = request.session.get("rw_active_chat_id")
    if not chat_id:
        return ctx

    try:
        chat = ChatWorkspace.objects.get(pk=chat_id)
    except ChatWorkspace.DoesNotExist:
        return ctx

    ctx["active_chat"] = chat
    ctx["turn_count"] = ChatMessage.objects.filter(
        chat=chat,
        role=ChatMessage.Role.ASSISTANT,
    ).count()

    # ---- Language: ONLY show override if this chat explicitly set one ----
    rw_chat_overrides = request.session.get("rw_chat_overrides", {}) or {}
    per_chat = rw_chat_overrides.get(str(chat.id), {}) or {}

    lang_name = (per_chat.get("LANGUAGE_NAME") or "").strip()
    if lang_name:
        # Override present: show it (variant/code optional)
        ctx["rw_language"] = {
            "name": lang_name,
            "variant": (per_chat.get("LANGUAGE_VARIANT") or "").strip(),
            "code": (per_chat.get("LANGUAGE_CODE") or "").strip(),
        }
    else:
        # No override: let template fall back to profile defaults
        ctx["rw_language"] = None

    return ctx


def session_overrides_bar(request) -> Dict[str, Any]:
    user = getattr(request, "user", None)
    if not user or isinstance(user, AnonymousUser) or not user.is_authenticated:
        return {"rw_overrides": None}

    profile = getattr(user, "profile", None)

    categories = [
        ("COGNITIVE", "Cognitive", "cognitive_avatar"),
        ("INTERACTION", "Interaction", "interaction_avatar"),
        ("PRESENTATION", "Presentation", "presentation_avatar"),
        ("EPISTEMIC", "Epistemic", "epistemic_avatar"),
        ("PERFORMANCE", "Performance", "performance_avatar"),
        ("CHECKPOINTING", "Checkpointing", "checkpointing_avatar"),
    ]

    # Choices (for UI; topbar is read-only)
    choices: Dict[str, Any] = {}
    for key, _label, _pf in categories:
        qs = Avatar.objects.filter(category=key, is_active=True).order_by("name")
        choices[key] = [{"id": str(a.id), "name": a.name} for a in qs]

    # ------------------------------------------------------------
    # Chat-scoped session overrides (THIS CHAT ONLY)
    # Stored in session as:
    #   rw_chat_overrides = { "<chat_id>": { "COGNITIVE": "<avatar_id>", ... } }
    # ------------------------------------------------------------
    active_chat_id = request.session.get("rw_active_chat_id")
    chat_overrides = request.session.get("rw_chat_overrides", {}) or {}
    chat_current = {}
    if active_chat_id:
        chat_current = chat_overrides.get(str(active_chat_id), {}) or {}

    # Session overrides stored as str(Avatar.id) or None (chat-scoped)
    current: Dict[str, Optional[str]] = {}
    for key, _label, _pf in categories:
        current[key] = chat_current.get(key)

    # NEW: flag if ANY override is active for this chat (including language name override)
    language_override_active = bool((chat_current.get("LANGUAGE_NAME") or "").strip())
    avatar_override_active = any(bool(current.get(k)) for (k, _lbl, _pf) in categories)
    chat_overrides_active = bool(active_chat_id and (language_override_active or avatar_override_active))

    # NEW: per-category override flags (for colouring individual boxes)
    overridden: Dict[str, bool] = {}
    for key, _label, _pf in categories:
        overridden[key] = bool(current.get(key))

    # Project-scoped prefs (optional override layer)
    active_project_id = request.session.get("rw_active_project_id")
    prefs = None
    if active_project_id is not None:
        try:
            pid = int(active_project_id)
        except (TypeError, ValueError):
            pid = None
        if pid is not None:
            prefs = (
                UserProjectPrefs.objects
                .filter(project_id=pid, user_id=user.id)
                .select_related(
                    "cognitive_avatar",
                    "interaction_avatar",
                    "presentation_avatar",
                    "epistemic_avatar",
                    "performance_avatar",
                    "checkpointing_avatar",
                )
                .first()
            )

    # Effective values shown in topbar (chat-session > project > profile > Default)
    defaults: Dict[str, str] = {}
    for key, _label, profile_field in categories:
        # 1) chat-scoped session override
        override_id = current.get(key)

        # Guard: ignore legacy/invalid override values (must look like an integer id)
        if override_id:
            override_id_str = str(override_id)
            if override_id_str.isdigit():
                av = Avatar.objects.filter(id=int(override_id_str)).only("name").first()
                if av:
                    defaults[key] = av.name
                    continue
            else:
                # invalid value in session; ignore it
                pass

        # 2) project override
        if prefs is not None:
            pav = getattr(prefs, profile_field, None)
            if pav is not None:
                defaults[key] = pav.name
                continue

        # 3) profile default
        default_name = "Default"
        if profile is not None:
            av = getattr(profile, profile_field, None)
            if av is not None:
                default_name = av.name
        defaults[key] = default_name

    return {
        "rw_overrides": {
            "categories": [(k, lbl) for (k, lbl, _pf) in categories],
            "choices": choices,
            "current": current,             # chat-scoped override ids (if any)
            "defaults": defaults,           # resolved names shown in topbar
            # NEW:
            "overridden": overridden,       # per-axis override booleans
            "chat_overrides_active": chat_overrides_active,  # any override active for this chat
        }
    }


def active_project_bar(request) -> Dict[str, Any]:
    """
    Provides:
      - rw_projects.choices: accessible projects for selector
      - rw_projects.active: active Project object or None
    Active project is stored in session as rw_active_project_id.
    """
    user = getattr(request, "user", None)
    if not user or isinstance(user, AnonymousUser) or not user.is_authenticated:
        return {"rw_projects": None}

    if user.is_superuser or user.is_staff:
        qs = accessible_projects_qs(request.user)
    else:
        qs = accessible_projects_qs(request.user).filter(Q(owner=user) | Q(scoped_roles__user=user)).distinct()

    projects = list(qs.order_by("name").only("id", "name", "kind"))

    active_id = request.session.get("rw_active_project_id")
    active: Optional[Project] = None
    if active_id is not None:
        try:
            active_id_int = int(active_id)
        except (TypeError, ValueError):
            active_id_int = None

        if active_id_int is not None:
            for p in projects:
                if p.id == active_id_int:
                    active = p
                    break

    # If no active selected, default to first sandbox if present, else first project, else None.
    if active is None and projects:
        sandbox = next((p for p in projects if getattr(p, "kind", None) == Project.Kind.SANDBOX), None)
        active = sandbox or projects[0]
        request.session["rw_active_project_id"] = active.id
        request.session.modified = True

    return {
        "rw_projects": {
            "choices": projects,
            "active": active,
        }
    }


def active_chat_bar(request) -> Dict[str, Any]:
    """
    Provides:
      - rw_chat.active_id: active chat id (or None)
      - rw_chat.chat_title: title of active chat (or empty)
      - rw_chat.turn_count: number of ASSISTANT messages
        (one per completed turn)

    Active chat is stored in session as rw_active_chat_id.
    """
    user = getattr(request, "user", None)
    if not user or isinstance(user, AnonymousUser) or not user.is_authenticated:
        return {"rw_chat": None}

    chat_id = request.session.get("rw_active_chat_id")
    if not chat_id:
        return {"rw_chat": {"active_id": None, "chat_title": "", "turn_count": 0}}

    try:
        chat_id_int = int(chat_id)
    except (TypeError, ValueError):
        return {"rw_chat": {"active_id": None, "chat_title": "", "turn_count": 0}}

    # Import inside function (keeps import order clean)
    from chats.models import ChatWorkspace, ChatMessage

    chat = (
        ChatWorkspace.objects
        .filter(pk=chat_id_int)
        .only("id", "title")
        .first()
    )

    if chat is None:
        request.session.pop("rw_active_chat_id", None)
        request.session.modified = True
        return {"rw_chat": {"active_id": None, "chat_title": "", "turn_count": 0}}

    msgs = ChatMessage.objects.filter(
        chat_id=chat.id
    ).order_by("sequence", "id").values_list("role", flat=True)

    pending_user = 0
    turn_count = 0

    for role in msgs:
        r = (role or "").upper()
        if r == "USER":
            pending_user += 1
        elif r == "ASSISTANT" and pending_user > 0:
            turn_count += 1
            pending_user -= 1

    return {
        "rw_chat": {
            "active_id": chat.id,
            "chat_title": chat.title or "",
            "turn_count": turn_count,
        }
    }
