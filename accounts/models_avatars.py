# -*- coding: utf-8 -*-
# accounts/models_avatars.py

from __future__ import annotations

from django.conf import settings
from django.db import models


class Avatar(models.Model):
    """
    Admin-managed avatar definition.

    One table, multiple categories.
    Users select one avatar per section.
    """

    class Category(models.TextChoices):
        # -------- Legacy --------
        COGNITIVE = "COGNITIVE", "Cognitive"
        INTERACTION = "INTERACTION", "Interaction"
        PRESENTATION = "PRESENTATION", "Presentation"
        EPISTEMIC = "EPISTEMIC", "Epistemic"
        PERFORMANCE = "PERFORMANCE", "Performance"
        CHECKPOINTING = "CHECKPOINTING", "Checkpointing"

        # -------- Avatar v2 --------
        TONE = "TONE", "Tone"
        REASONING = "REASONING", "Reasoning"
        APPROACH = "APPROACH", "Approach"
        CONTROL = "CONTROL", "Control"

    category = models.CharField(max_length=20, choices=Category.choices)
    key = models.SlugField(max_length=80)  # unique per category
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
    - Preferred language + variant
    - Legacy avatars (unchanged)
    - Avatar v2 axes (Tone, Reasoning, Approach, Control)
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )

    # Language preferences
    default_language = models.CharField(max_length=40, default="English")
    default_language_variant = models.CharField(max_length=40, default="British English")

    language_switching_permitted = models.BooleanField(default=True)
    persist_language_switch_for_session = models.BooleanField(default=True)
    llm_provider = models.CharField(
        max_length=20,
        choices=[("openai", "OpenAI"), ("anthropic", "Anthropic"), ("copilot", "Copilot")],
        default="openai",
    )
    openai_model_default = models.CharField(
        max_length=80,
        blank=True,
        default="gpt-5.1",
    )
    anthropic_model_default = models.CharField(
        max_length=80,
        blank=True,
        default="claude-sonnet-4-5-20250929",
    )

    # -------- Legacy avatar fields (unchanged) --------

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

    # -------- Avatar v2 fields --------

    tone_avatar = models.ForeignKey(
        Avatar,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="users_tone",
        limit_choices_to={"category": Avatar.Category.TONE, "is_active": True},
    )
    reasoning_avatar = models.ForeignKey(
        Avatar,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="users_reasoning",
        limit_choices_to={"category": Avatar.Category.REASONING, "is_active": True},
    )
    approach_avatar = models.ForeignKey(
        Avatar,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="users_approach",
        limit_choices_to={"category": Avatar.Category.APPROACH, "is_active": True},
    )
    control_avatar = models.ForeignKey(
        Avatar,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="users_control",
        limit_choices_to={"category": Avatar.Category.CONTROL, "is_active": True},
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["default_language_variant"]),
        ]

    def __str__(self) -> str:
        return f"Profile:{self.user_id}"

