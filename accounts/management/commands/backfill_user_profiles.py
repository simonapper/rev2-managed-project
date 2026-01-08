# -*- coding: utf-8 -*-
# accounts/management/commands/backfill_user_profiles.py

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from django.contrib.auth import get_user_model

from accounts.models_avatars import Avatar, UserProfile


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


def ensure_default_avatars() -> None:
    for category, key, name in DEFAULT_AVATARS:
        Avatar.objects.get_or_create(
            category=category,
            key=key,
            defaults={"name": name, "is_active": True},
        )


def get_default_avatar(category: str, key: str) -> Avatar:
    return Avatar.objects.get(category=category, key=key, is_active=True)


class Command(BaseCommand):
    help = "Backfill UserProfile rows for existing users (and ensure default Avatars)."

    def handle(self, *args, **options):
        User = get_user_model()

        with transaction.atomic():
            ensure_default_avatars()

            cognitive = get_default_avatar(Avatar.Category.COGNITIVE, "analyst")
            interaction = get_default_avatar(Avatar.Category.INTERACTION, "minimal")
            presentation = get_default_avatar(Avatar.Category.PRESENTATION, "laptop")
            epistemic = get_default_avatar(Avatar.Category.EPISTEMIC, "canonical")
            performance = get_default_avatar(Avatar.Category.PERFORMANCE, "tight")
            checkpointing = get_default_avatar(Avatar.Category.CHECKPOINTING, "manual")

            created_count = 0
            for u in User.objects.all():
                _, created = UserProfile.objects.get_or_create(
                    user=u,
                    defaults={
                        "default_language": "English",
                        "default_language_variant": UserProfile.LanguageVariant.EN_GB,
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
                if created:
                    created_count += 1

        self.stdout.write(self.style.SUCCESS(f"Backfill complete. Created {created_count} profile(s)."))
