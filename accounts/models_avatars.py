# -*- coding: utf-8 -*-
# accounts/models_avatars.py

from __future__ import annotations

from django.conf import settings
from django.db import models


class Avatar(models.Model):
    """
    Admin-managed avatar definition.

    One table, multiple categories.
    Users select one avatar per L1 section.
    """

    class Category(models.TextChoices):
        COGNITIVE = "COGNITIVE", "Cognitive"
        INTERACTION = "INTERACTION", "Interaction"
        PRESENTATION = "PRESENTATION", "Presentation"
        EPISTEMIC = "EPISTEMIC", "Epistemic"
        PERFORMANCE = "PERFORMANCE", "Performance"
        CHECKPOINTING = "CHECKPOINTING", "Checkpointing"

    category = models.CharField(max_length=20, choices=Category.choices)
    key = models.SlugField(max_length=80)  # unique per category (enforced below)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("category", "key")]
        indexes = [
            models.Index(fields=["category", "is_active"]),
            models.Index(fields=["category", "key"]),
        ]

    def __str__(self) -> str:
        return self.name


class UserProfile(models.Model):
    """
    User-visible configuration knobs for prototype:
    - Preferred language + variant (user-defined free text)
    - One avatar choice per L1 section
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )

    # User-preference language fields (free text)
    default_language = models.CharField(max_length=40, default="English")
    default_language_variant = models.CharField(max_length=40, default="British English")

    language_switching_permitted = models.BooleanField(default=True)
    persist_language_switch_for_session = models.BooleanField(default=True)

    cognitive_avatar = models.ForeignKey(
        Avatar,
        on_delete=models.PROTECT,
        related_name="users_cognitive",
        limit_choices_to={"category": Avatar.Category.COGNITIVE, "is_active": True},
    )
    interaction_avatar = models.ForeignKey(
        Avatar,
        on_delete=models.PROTECT,
        related_name="users_interaction",
        limit_choices_to={"category": Avatar.Category.INTERACTION, "is_active": True},
    )
    presentation_avatar = models.ForeignKey(
        Avatar,
        on_delete=models.PROTECT,
        related_name="users_presentation",
        limit_choices_to={"category": Avatar.Category.PRESENTATION, "is_active": True},
    )
    epistemic_avatar = models.ForeignKey(
        Avatar,
        on_delete=models.PROTECT,
        related_name="users_epistemic",
        limit_choices_to={"category": Avatar.Category.EPISTEMIC, "is_active": True},
    )
    performance_avatar = models.ForeignKey(
        Avatar,
        on_delete=models.PROTECT,
        related_name="users_performance",
        limit_choices_to={"category": Avatar.Category.PERFORMANCE, "is_active": True},
    )
    checkpointing_avatar = models.ForeignKey(
        Avatar,
        on_delete=models.PROTECT,
        related_name="users_checkpointing",
        limit_choices_to={"category": Avatar.Category.CHECKPOINTING, "is_active": True},
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["default_language_variant"]),
        ]

    def __str__(self) -> str:
        return f"Profile:{self.user_id}"
