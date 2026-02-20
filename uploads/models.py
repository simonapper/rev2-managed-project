# -*- coding: utf-8 -*-
# uploads/models.py

from __future__ import annotations

import os
import mimetypes
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


def generated_image_upload_to(instance: "GeneratedImage", filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if not ext:
        guessed = mimetypes.guess_extension((instance.mime_type or "").strip()) or ".png"
        ext = guessed.lower()
    safe = (instance.sha256 or uuid4().hex)[:64] + ext
    project_id = instance.project_id or "none"
    chat_id = instance.chat_id or "none"
    return f"projects/{project_id}/chats/{chat_id}/generated_images/{safe}"


class GeneratedImage(models.Model):
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="generated_images",
        null=True,
        blank=True,
    )
    chat = models.ForeignKey(
        "chats.ChatWorkspace",
        on_delete=models.CASCADE,
        related_name="generated_images",
        null=True,
        blank=True,
    )
    message = models.ForeignKey(
        "chats.ChatMessage",
        on_delete=models.SET_NULL,
        related_name="generated_images",
        null=True,
        blank=True,
    )

    provider = models.CharField(max_length=30, blank=True, default="")
    model = models.CharField(max_length=120, blank=True, default="")
    prompt = models.TextField(blank=True, default="")
    file_id = models.CharField(max_length=200, blank=True, default="")

    mime_type = models.CharField(max_length=80, blank=True, default="image/png")
    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)
    sha256 = models.CharField(max_length=64, blank=True, default="", db_index=True)
    image_file = models.FileField(upload_to=generated_image_upload_to)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["project", "created_at"]),
            models.Index(fields=["chat", "created_at"]),
            models.Index(fields=["message", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"GeneratedImage:{self.id}:{self.provider}:{self.model}"
