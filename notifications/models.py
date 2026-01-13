# notifications/models.py
# -*- coding: utf-8 -*-

from django.conf import settings
from django.db import models


class Notification(models.Model):
    class Type(models.TextChoices):
        NEEDS_APPROVAL = "NEEDS_APPROVAL", "Needs approval"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        INFO = "INFO", "Info"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
    )

    type = models.CharField(max_length=30, choices=Type.choices, default=Type.INFO)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)

    # Optional deep links
    link_object = models.ForeignKey(
        "objects.KnowledgeObject",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    link_url = models.CharField(max_length=500, blank=True)

    is_read = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["recipient", "is_read", "created_at"]),
            models.Index(fields=["project", "created_at"]),
            models.Index(fields=["type"]),
        ]

    def __str__(self) -> str:
        return f"{self.recipient_id} {self.type} {self.title}"
