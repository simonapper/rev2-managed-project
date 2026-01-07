# -*- coding: utf-8 -*-
# projects/models.py

from django.conf import settings
from django.db import models


class Project(models.Model):
    """
    Top-level container.
    Everything meaningful lives inside a Project.
    """

    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)

    # Owner is accountable for the project (stewardship/provenance).
    # Authority should be enforced via UserRole (project-scoped).
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_projects",
    )

    # Active Level-4 (session) configuration for this project.
    # This points to a ConfigRecord (Level 4) whose ConfigVersion holds the
    # actual .session.conf content.
    active_l4_config = models.ForeignKey(
        "config.ConfigRecord",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="projects_using_as_l4",
        limit_choices_to={"level": 4},
        help_text="Active Level-4 (session) configuration for this project.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name


class Folder(models.Model):
    """
    Lightweight hierarchy for organising chats.
    This is navigational only, not semantic.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="folders")

    # Self-referential parent for tree structure
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )

    name = models.CharField(max_length=200)
    ordering = models.IntegerField(default=0)

    class Meta:
        # Prevent duplicate names at the same tree level
        unique_together = [("project", "parent", "name")]
        indexes = [
            models.Index(fields=["project", "ordering"]),
            models.Index(fields=["project", "parent"]),
        ]

    def __str__(self) -> str:
        return f"{self.project}: {self.name}"
