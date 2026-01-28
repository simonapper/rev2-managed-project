# -*- coding: utf-8 -*-
# config/models.py

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class ConfigScope(models.Model):
    """
    Defines the scope boundary for a configuration.

    v1 scope model:
    - ORG     : global (project/user/session_id must be empty)
    - PROJECT : scoped to one project
    - USER    : scoped to one user (personal defaults/overrides)
    - SESSION : scoped to a single session run (ephemeral)
    """

    class ScopeType(models.TextChoices):
        ORG = "ORG", "Organisation"
        PROJECT = "PROJECT", "Project"
        USER = "USER", "User"
        SESSION = "SESSION", "Session"

    scope_type = models.CharField(max_length=20, choices=ScopeType.choices)

    # Populated only when scope_type == PROJECT
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )

    # Populated only when scope_type == USER
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )

    # Populated only when scope_type == SESSION
    session_id = models.CharField(max_length=120, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["scope_type"]),
            models.Index(fields=["project"]),
            models.Index(fields=["user"]),
        ]

    def __str__(self) -> str:
        if self.scope_type == self.ScopeType.ORG:
            return "ORG"
        if self.scope_type == self.ScopeType.PROJECT:
            return f"PROJECT:{self.project_id}"
        if self.scope_type == self.ScopeType.USER:
            return f"USER:{self.user_id}"
        return f"SESSION:{self.session_id}"


class ConfigRecord(models.Model):
    """
    Represents a logical configuration file (Level 1–4) at a given scope.

    Examples:
    - Level 3, file_id=L3-SOV-ROUTE-001, scope=ORG
    - Level 4, file_id=L4-DEFAULTS, scope=ORG
    - Level 4, file_id=L4-USER-DEFAULTS, scope=USER:123
    """

    class Level(models.IntegerChoices):
        L1 = 1, "Level 1"
        L2 = 2, "Level 2"
        L3 = 3, "Level 3"
        L4 = 4, "Level 4"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"

    level = models.IntegerField(choices=Level.choices)

    file_id = models.CharField(max_length=120)
    file_name = models.CharField(max_length=200)

    display_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Human-readable name shown in the UI",
    )

    scope = models.ForeignKey(
        ConfigScope,
        on_delete=models.CASCADE,
        related_name="configs",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_configs",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("level", "file_id", "scope")]
        indexes = [
            models.Index(fields=["level", "status"]),
            models.Index(fields=["file_id"]),
        ]

    def clean(self) -> None:
        """
        Enforce valid Level ↔ Scope combinations.
        """

        if not self.scope:
            raise ValidationError({"scope": "Scope is required for all configuration records."})

        # IMPORTANT: Updated to support:
        # - System defaults at ORG for any level where needed
        # - User defaults at USER for L4 (and possibly L1)
        # - Project overrides at PROJECT (esp L4)
        # - Session overrides at SESSION (esp L4)
        allowed_scopes_by_level = {
            self.Level.L1: {ConfigScope.ScopeType.USER, ConfigScope.ScopeType.ORG},
            self.Level.L2: {ConfigScope.ScopeType.ORG},
            self.Level.L3: {ConfigScope.ScopeType.ORG},
            self.Level.L4: {
                ConfigScope.ScopeType.ORG,
                ConfigScope.ScopeType.USER,
                ConfigScope.ScopeType.PROJECT,
                ConfigScope.ScopeType.SESSION,
            },
        }

        allowed_scopes = allowed_scopes_by_level.get(self.level, set())

        if self.scope.scope_type not in allowed_scopes:
            raise ValidationError(
                {"scope": f"Level {self.level} configs cannot be scoped to {self.scope.scope_type}."}
            )

        # (Optional but sensible) enforce scope-field combinations:
        # - ORG must have no project/user/session_id
        # - PROJECT must have project set and no user/session_id
        # - USER must have user set and no project/session_id
        # - SESSION must have session_id set and no project/user
        st = self.scope.scope_type
        if st == ConfigScope.ScopeType.ORG:
            if self.scope.project_id or self.scope.user_id or self.scope.session_id:
                raise ValidationError({"scope": "ORG scope cannot have project/user/session_id set."})
        elif st == ConfigScope.ScopeType.PROJECT:
            if not self.scope.project_id or self.scope.user_id or self.scope.session_id:
                raise ValidationError({"scope": "PROJECT scope requires project only."})
        elif st == ConfigScope.ScopeType.USER:
            if not self.scope.user_id or self.scope.project_id or self.scope.session_id:
                raise ValidationError({"scope": "USER scope requires user only."})
        elif st == ConfigScope.ScopeType.SESSION:
            if not self.scope.session_id or self.scope.project_id or self.scope.user_id:
                raise ValidationError({"scope": "SESSION scope requires session_id only."})

    def save(self, *args, **kwargs) -> None:
        # Enforce semantic validation everywhere (admin, scripts, services)
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        scope = self.scope.scope_type
        if scope == ConfigScope.ScopeType.PROJECT:
            scope = f"Project: {self.scope.project}"
        elif scope == ConfigScope.ScopeType.USER:
            scope = f"User: {self.scope.user}"
        elif scope == ConfigScope.ScopeType.SESSION:
            scope = f"Session: {self.scope.session_id}"
        return f"{self.file_id} ({scope})"


class ConfigVersion(models.Model):
    """
    Immutable snapshot of a config file's contents.

    content_text stores the canonical text block (your 'conf' format).
    version may be semantic or incremental.
    """

    config = models.ForeignKey(
        ConfigRecord,
        on_delete=models.CASCADE,
        related_name="versions",
    )

    version = models.CharField(max_length=30, default="0.0.0")
    content_text = models.TextField()
    change_note = models.CharField(max_length=500, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="config_versions",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("config", "version")]
        indexes = [
            models.Index(fields=["config", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.config.file_id}@{self.version}"


class SystemConfigPointers(models.Model):
    """
    Singleton row holding the active ConfigRecord per level at ORG scope.
    Superuser UI edits these pointers (not resolver magic).

    Convention: create exactly one row (id=1).
    """

    active_l1_config = models.ForeignKey(
        ConfigRecord,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        limit_choices_to={"level": ConfigRecord.Level.L1},
    )
    active_l2_config = models.ForeignKey(
        ConfigRecord,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        limit_choices_to={"level": ConfigRecord.Level.L2},
    )
    active_l3_config = models.ForeignKey(
        ConfigRecord,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        limit_choices_to={"level": ConfigRecord.Level.L3},
    )
    active_l4_config = models.ForeignKey(
        ConfigRecord,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        limit_choices_to={"level": ConfigRecord.Level.L4},
    )
    # OpenAI model selection (used by chats.services.llm.generate_panes)
    openai_model_default = models.CharField(
        max_length=80,
        blank=True,
        default="gpt-5.1",
        help_text="Default OpenAI model key, e.g. gpt-5.1, gpt-4.1-mini, o4-mini",
    )

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="system_config_pointer_updates",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "System config pointers"
        verbose_name_plural = "System config pointers"

    def __str__(self) -> str:
        return "SystemConfigPointers"

