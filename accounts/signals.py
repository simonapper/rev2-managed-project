# -*- coding: utf-8 -*-
# accounts/signals.py

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from config.models import ConfigScope, ConfigRecord, ConfigVersion
from accounts.models_avatars import Avatar, UserProfile


UserModel = get_user_model()


DEFAULT_L1_USER_SETTINGS_TEXT = """# ============================================================
# USER SETTINGS - DEFAULT
# Purpose: Default user preferences.
# Scope: Level 1 - User
# ============================================================

Cognitive Profile
- Explicit structure preferred: ON
- Clear stage separation: ON
- Logic over persuasion: ON
- Fidelity over coherence: ON

Interaction Preferences
- Language: English: ON
- Language Variant: British English: ON
- Precise responses: ON
- Low verbosity by default: ON
- No rhetorical padding: ON
- Pushback style: Firm but non-judgemental: ON
"""


DEFAULT_AVATARS = [
    # Cognitive
    (Avatar.Category.COGNITIVE, "analyst", "Analyst"),
    (Avatar.Category.COGNITIVE, "artist", "Artist"),
    (Avatar.Category.COGNITIVE, "advocate", "Advocate"),
    (Avatar.Category.COGNITIVE, "explorer", "Explorer"),
    # Interaction
    (Avatar.Category.INTERACTION, "minimal", "Minimal"),
    (Avatar.Category.INTERACTION, "concise", "Concise"),
    (Avatar.Category.INTERACTION, "socratic", "Socratic"),
    (Avatar.Category.INTERACTION, "didactic", "Didactic"),
    (Avatar.Category.INTERACTION, "conversational", "Conversational"),
    # Presentation
    (Avatar.Category.PRESENTATION, "phone", "Phone"),
    (Avatar.Category.PRESENTATION, "laptop", "Laptop"),
    (Avatar.Category.PRESENTATION, "tablet", "Tablet"),
    (Avatar.Category.PRESENTATION, "multi-screen", "Multi-Screen"),
    # Epistemic
    (Avatar.Category.EPISTEMIC, "canonical", "Canonical"),
    (Avatar.Category.EPISTEMIC, "analytical", "Analytical"),
    (Avatar.Category.EPISTEMIC, "exploratory", "Exploratory"),
    (Avatar.Category.EPISTEMIC, "advocacy", "Advocacy"),
    # Performance
    (Avatar.Category.PERFORMANCE, "tight", "Tight"),
    (Avatar.Category.PERFORMANCE, "balanced", "Balanced"),
    (Avatar.Category.PERFORMANCE, "expansive", "Expansive"),
    # Checkpointing
    (Avatar.Category.CHECKPOINTING, "manual", "Manual"),
    (Avatar.Category.CHECKPOINTING, "assisted", "Assisted"),
    (Avatar.Category.CHECKPOINTING, "automatic", "Automatic"),
]


def _ensure_user_dirs(user_id: int) -> None:
    base = Path(settings.MEDIA_ROOT) / "aiscape" / "users" / str(user_id)
    for sub in ("uploads", "exports", "tmp"):
        (base / sub).mkdir(parents=True, exist_ok=True)


def _ensure_default_avatars() -> None:
    for category, key, name in DEFAULT_AVATARS:
        Avatar.objects.get_or_create(
            category=category,
            key=key,
            defaults={"name": name, "is_active": True},
        )


def _get_default_avatar(category: str, key: str) -> Avatar:
    return Avatar.objects.get(category=category, key=key, is_active=True)


def _ensure_user_profile(user: AbstractUser) -> None:
    _ensure_default_avatars()

    cognitive = _get_default_avatar(Avatar.Category.COGNITIVE, "analyst")
    interaction = _get_default_avatar(Avatar.Category.INTERACTION, "minimal")
    presentation = _get_default_avatar(Avatar.Category.PRESENTATION, "laptop")
    epistemic = _get_default_avatar(Avatar.Category.EPISTEMIC, "canonical")
    performance = _get_default_avatar(Avatar.Category.PERFORMANCE, "tight")
    checkpointing = _get_default_avatar(Avatar.Category.CHECKPOINTING, "manual")

    UserProfile.objects.get_or_create(
        user=user,
        defaults={
            "default_language": "English",
            "default_language_variant": "British English",
            "language_switching_permitted": True,
            "persist_language_switch_for_session": True,
            "cognitive_avatar": cognitive,
            "interaction_avatar": interaction,
            "presentation_avatar": presentation,
            "epistemic_avatar": epistemic,
            "performance_avatar": performance,
            "checkpointing_avatar": checkpointing,
        },
    )


def _ensure_default_l1_config(user: AbstractUser) -> None:
    scope, _ = ConfigScope.objects.get_or_create(
        scope_type=ConfigScope.ScopeType.USER,
        user=user,
        defaults={"session_id": ""},
    )

    record, created = ConfigRecord.objects.get_or_create(
        level=ConfigRecord.Level.L1,
        file_id="USER-SETTINGS",
        scope=scope,
        defaults={
            "file_name": "user_settings.conf",
            "status": ConfigRecord.Status.ACTIVE,
            "created_by": user,
        },
    )

    if created:
        ConfigVersion.objects.create(
            config=record,
            version="0.1.0",
            content_text=DEFAULT_L1_USER_SETTINGS_TEXT,
            change_note="Auto-created default Level 1 user settings.",
            created_by=user,
        )


@receiver(post_save, sender=UserModel)
def user_post_create(sender, instance: AbstractUser, created: bool, **kwargs) -> None:
    if not created:
        return

    # DB-side defaults in one transaction
    with transaction.atomic():
        _ensure_user_profile(instance)
        _ensure_default_l1_config(instance)

    # Filesystem outside DB txn
    _ensure_user_dirs(instance.pk)
