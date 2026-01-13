# -*- coding: utf-8 -*-
# accounts/context_processors.py

from __future__ import annotations

from typing import Any, Dict, Optional

from django.contrib.auth.models import AnonymousUser
from django.db.models import Q

from accounts.models_avatars import Avatar
from projects.models import Project
from projects.services import accessible_projects_qs

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

    # Choices (for future UI; currently topbar is read-only)
    choices: Dict[str, Any] = {}
    for key, _label, _pf in categories:
        qs = Avatar.objects.filter(category=key, is_active=True).order_by("name")
        choices[key] = [{"id": str(a.id), "name": a.name} for a in qs]

    # Session overrides stored as str(Avatar.id) or None
    current: Dict[str, Optional[str]] = {}
    for key, _label, _pf in categories:
        current[key] = request.session.get(f"rw_l4_override_{key}")

    # Defaults from profile (names)
    defaults: Dict[str, str] = {}
    for key, _label, profile_field in categories:
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
            "current": current,
            "defaults": defaults,
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
      - rw_chat.turn_count: number of USER messages in active chat (MVP "interaction" count)

    Active chat is stored in session as rw_active_chat_id.
    """
    user = getattr(request, "user", None)
    if not user or isinstance(user, AnonymousUser) or not user.is_authenticated:
        return {"rw_chat": None}

    chat_id = request.session.get("rw_active_chat_id")
    if not chat_id:
        return {"rw_chat": {"active_id": None, "turn_count": 0}}

    try:
        chat_id_int = int(chat_id)
    except (TypeError, ValueError):
        return {"rw_chat": {"active_id": None, "turn_count": 0}}

    # Import inside function (keeps import order clean)
    from chats.models import ChatWorkspace, ChatMessage

    chat = ChatWorkspace.objects.filter(pk=chat_id_int).only("id", "project_id").first()
    if chat is None:
        request.session.pop("rw_active_chat_id", None)
        request.session.modified = True
        return {"rw_chat": {"active_id": None, "turn_count": 0}}

    turn_count = ChatMessage.objects.filter(chat_id=chat.id, role=ChatMessage.Role.USER).count()

    return {"rw_chat": {"active_id": chat.id, "turn_count": turn_count}}
