# -*- coding: utf-8 -*-
# projects/context.py
# Purpose:
# Resolve the effective runtime context for a user within a project.

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model

from projects.models import Project, UserProjectPrefs
from django.contrib.auth.models import AbstractUser

UserModel = get_user_model()


def resolve_effective_context(
    *,
    project: Project,
    user: AbstractUser,
    session_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Resolve the effective working context for this user in this project.

    Precedence (highest wins, unless forbidden):
      SESSION > UserProjectPrefs > ProjectPolicy > inherited defaults
    """

    policy = project.policy
    prefs = (
        UserProjectPrefs.objects
        .filter(project=project, user=user)
        .first()
    )
    session = session_overrides or {}

    ctx: dict[str, Any] = {}

    # --------------------------------------------------
    # 1) Inherited defaults (via ProjectPolicy)
    # --------------------------------------------------

    ctx["language"] = policy.language_default
    ctx["output_format"] = policy.output_format_default
    ctx["checkpointing"] = "standard"
    ctx["verbosity"] = ""
    ctx["tone"] = ""
    ctx["formatting"] = ""

    # --------------------------------------------------
    # 2) UserProjectPrefs (soft, gated)
    # --------------------------------------------------

    if prefs:
        if policy.user_can_override_language and prefs.active_language:
            ctx["language"] = prefs.active_language

        if policy.user_can_override_checkpointing and prefs.checkpointing_override:
            ctx["checkpointing"] = prefs.checkpointing_override

        if prefs.verbosity:
            ctx["verbosity"] = prefs.verbosity

        if prefs.tone:
            ctx["tone"] = prefs.tone

        if prefs.formatting:
            ctx["formatting"] = prefs.formatting

    # --------------------------------------------------
    # 3) Session overrides (ephemeral, highest)
    # --------------------------------------------------

    if policy.user_can_override_language and "language" in session:
        ctx["language"] = session["language"]

    if policy.user_can_override_checkpointing and "checkpointing" in session:
        ctx["checkpointing"] = session["checkpointing"]

    if policy.user_can_override_output_format and "output_format" in session:
        ctx["output_format"] = session["output_format"]

    # --------------------------------------------------
    # 4) Hard rails enforcement (final)
    # --------------------------------------------------

    if not policy.user_can_override_language:
        ctx["language"] = policy.language_default

    if not policy.user_can_override_checkpointing:
        ctx["checkpointing"] = "standard"

    if not policy.user_can_override_output_format:
        ctx["output_format"] = policy.output_format_default

    # --------------------------------------------------
    # 5) Project-only flags (never overridden)
    # --------------------------------------------------

    ctx["feature_flags"] = policy.feature_flags or {}

    return ctx
