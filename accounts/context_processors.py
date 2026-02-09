# -*- coding: utf-8 -*-
# accounts/context_processors.py

from __future__ import annotations

from typing import Any, Dict, Optional

from django.contrib.auth.models import AnonymousUser
from django.db.models import Q

from accounts.models_avatars import Avatar
from projects.models import Project, UserProjectPrefs
from projects.services_project_membership import accessible_projects_qs
from chats.models import ChatMessage, ChatWorkspace
from projects.services.context_resolution import resolve_effective_context

def topbar_context(request) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {}

    user = getattr(request, "user", None)
    if not user or isinstance(user, AnonymousUser) or not user.is_authenticated:
        return ctx

    # ------------------------------------------------------------
    # Active project (session -> URL fallback)
    # ------------------------------------------------------------
    active_project = None

    # 1) From session
    pid = request.session.get("rw_active_project_id")
    if pid is not None:
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            pid_int = None
        if pid_int is not None:
            active_project = Project.objects.filter(id=pid_int).only("id", "name").first()

    # 2) Fallback from URL (authoritative when switching projects)
    if active_project is None and hasattr(request, "resolver_match"):
        kwargs = request.resolver_match.kwargs or {}
        project_id = kwargs.get("project_id")
        if project_id:
            try:
                pid_int = int(project_id)
                active_project = Project.objects.filter(id=pid_int).only("id", "name").first()
                if active_project:
                    request.session["rw_active_project_id"] = pid_int
                    request.session.modified = True
            except (TypeError, ValueError):
                pass

    if active_project is not None:
        ctx["rw_projects"] = {"active": active_project}

    # ------------------------------------------------------------
    # Active chat (optional)
    # ------------------------------------------------------------
    chat_id = request.session.get("rw_active_chat_id")
    if not chat_id:
        return ctx

    try:
        chat_id_int = int(chat_id)
    except (TypeError, ValueError):
        return ctx

    chat = ChatWorkspace.objects.filter(pk=chat_id_int).select_related("project").first()
    if chat is None:
        return ctx

    ctx["active_chat"] = chat

    # Sync project to chat.project when viewing a chat
    if "rw_projects" not in ctx or ctx.get("rw_projects", {}).get("active") != chat.project:
        ctx["rw_projects"] = {"active": chat.project}
        request.session["rw_active_project_id"] = chat.project.id
        request.session.modified = True

    ctx["turn_count"] = ChatMessage.objects.filter(
        chat=chat,
        role=ChatMessage.Role.ASSISTANT,
    ).count()

    # ---- Language override: ONLY show if this chat explicitly set one ----
    rw_chat_overrides = request.session.get("rw_chat_overrides", {}) or {}
    per_chat = rw_chat_overrides.get(str(chat.id), {}) or {}

    lang_name = (per_chat.get("LANGUAGE_NAME") or "").strip()
    if lang_name:
        ctx["rw_language"] = {
            "name": lang_name,
            "variant": (per_chat.get("LANGUAGE_VARIANT") or "").strip(),
            "code": (per_chat.get("LANGUAGE_CODE") or "").strip(),
        }
    else:
        ctx["rw_language"] = None

    return ctx



def session_overrides_bar(request) -> Dict[str, Any]:
    user = getattr(request, "user", None)
    if not user or isinstance(user, AnonymousUser) or not user.is_authenticated:
        return {"rw_overrides": None, "rw_v2": None}

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

    # Chat-scoped overrides stored as str(Avatar.id) or None
    current: Dict[str, Optional[str]] = {}
    for key, _label, _pf in categories:
        current[key] = chat_current.get(key)

    # Flag if ANY override is active for this chat (including language name override)
    language_override_active = bool((chat_current.get("LANGUAGE_NAME") or "").strip())
    avatar_override_active = any(bool(current.get(k)) for (k, _lbl, _pf) in categories)
    chat_overrides_active = bool(active_chat_id and (language_override_active or avatar_override_active))

    # Per-category override flags (for colouring individual boxes)
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

    # ------------------------------------------------------------
    # Effective legacy values (for legacy UI/debug only):
    # Chat > Session > Project > Profile > Default
    # ------------------------------------------------------------
    defaults: Dict[str, str] = {}
    session_overrides = request.session.get("rw_session_overrides", {}) or {}

    for key, _label, profile_field in categories:
        # 1) chat-scoped override (this chat only)
        override_id = current.get(key)
        if override_id:
            override_id_str = str(override_id)
            if override_id_str.isdigit():
                av = Avatar.objects.filter(id=int(override_id_str)).only("name").first()
                if av:
                    defaults[key] = av.name
                    continue

        # 2) session-scoped override (all chats in this browser session)
        session_override_id = session_overrides.get(key)
        if session_override_id:
            so_str = str(session_override_id)
            if so_str.isdigit():
                av = Avatar.objects.filter(id=int(so_str)).only("name").first()
                if av:
                    defaults[key] = av.name
                    continue

        # 3) project override
        if prefs is not None:
            pav = getattr(prefs, profile_field, None)
            if pav is not None:
                defaults[key] = pav.name
                continue

        # 4) profile default
        default_name = "Default"
        if profile is not None:
            av = getattr(profile, profile_field, None)
            if av is not None:
                default_name = av.name
        defaults[key] = default_name

    # ------------------------------------------------------------
    # v2 tiles (authoritative, always safe)
    # ------------------------------------------------------------
    rw_v2 = {
        "tone": "Brief",
        "reasoning": "Careful",
        "approach": "Step-by-step",
        "control": "User",
    }

    try:
        active_project_id = request.session.get("rw_active_project_id")
        pid = int(active_project_id) if str(active_project_id).isdigit() else None

        chat_overrides_for_active = {}
        if active_chat_id:
            chat_overrides_for_active = chat_overrides.get(str(active_chat_id), {}) or {}

        if pid is not None:
            effective = resolve_effective_context(
                project_id=pid,
                user_id=user.id,
                session_overrides=session_overrides,
                chat_overrides=chat_overrides_for_active,
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

    return {
        "rw_overrides": {
            "categories": [(k, lbl) for (k, lbl, _pf) in categories],
            "choices": choices,
            "current": current,             # chat-scoped override ids (if any)
            "defaults": defaults,           # resolved legacy names (legacy UI/debug)
            "overridden": overridden,       # per-axis override booleans
            "chat_overrides_active": chat_overrides_active,
        },
        "rw_v2": rw_v2,
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
def active_project_bar(request):
    return {}
