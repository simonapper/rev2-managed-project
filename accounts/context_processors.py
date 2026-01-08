# -*- coding: utf-8 -*-
# accounts/context_processors.py

from __future__ import annotations

from typing import Any, Dict

from django.contrib.auth.models import AnonymousUser

from accounts.models_avatars import Avatar


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

    def qs(cat: str):
        return Avatar.objects.filter(category=cat, is_active=True).order_by("name")

    current: Dict[str, Any] = {}
    defaults: Dict[str, str] = {}
    choices: Dict[str, Any] = {}

    for key, _label, profile_field in categories:
        current[key] = request.session.get(f"rw_l4_override_{key}")  # stored as str(id) or None
        choices[key] = qs(getattr(Avatar.Category, key))

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
