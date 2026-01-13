# -*- coding: utf-8 -*-
# uploads/models.py

from __future__ import annotations

import os
from uuid import uuid4

from django.conf import settings
from django.db import models


def chat_attachment_upload_to(instance: "ChatAttachment", filename: str) -> str:
    """
    Store under:
      media/projects/<project_id>/chats/<chat_id>/uploads/<uuid>_<filename>
    """
    base, ext = os.path.splitext(filename)
    safe_name = f"{uuid4().hex}{ext.lower()}"
    return f"projects/{instance.project_id}/chats/{instance.chat_id}/uploads/{safe_name}"


class ChatAttachment(models.Model):
    """
    Durable file uploaded into a chat context.
    Stored locally (MEDIA). Optional future linkage to OpenAI via openai_file_id.
    """

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="chat_attachments",
    )

    chat = models.ForeignKey(
        "chats.ChatWorkspace",
        on_delete=models.CASCADE,
        related_name="attachments",
    )

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_chat_attachments",
    )

    file = models.FileField(upload_to=chat_attachment_upload_to)

    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120, blank=True, default="")
    size_bytes = models.PositiveBigIntegerField(default=0)

    # Phase 2 (optional): store OpenAI file id once uploaded upstream
    openai_file_id = models.CharField(max_length=120, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.original_name} ({self.size_bytes} bytes)"
