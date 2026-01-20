# chats/models.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from django.conf import settings
from django.db import models, transaction
from django.db.models import Max


class ChatWorkspace(models.Model):
    """
    Disposable conversational workspace.
    Chats are not durable knowledge; transcripts are retained.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        ARCHIVED = "ARCHIVED", "Archived"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="chats",
    )

    folder = models.ForeignKey(
        "projects.Folder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chats",
    )

    title = models.CharField(max_length=250)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_chats",
    )

    # Optional per-chat temporary overrides (do NOT store resolved policy here)
    chat_overrides = models.JSONField(default=dict, blank=True)

    # Convenience: cache last output for chat list tiles
    last_output_snippet = models.CharField(max_length=280, blank=True, default="")
    last_output_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.title


class ChatMessage(models.Model):
    """
    Single logical message in a chat.
    Raw text is authoritative; panes are derived at save time.
    """

    class Role(models.TextChoices):
        USER = "USER", "User"
        ASSISTANT = "ASSISTANT", "Assistant"
        SYSTEM = "SYSTEM", "System"

    chat = models.ForeignKey(
        "chats.ChatWorkspace",
        on_delete=models.CASCADE,
        related_name="messages",
    )

    sequence = models.PositiveIntegerField(
        help_text="Monotonic per-chat ordering",
    )

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
    )

    # Authoritative transcript payload
    raw_text = models.TextField()

    # Derived pane segments
    answer_text = models.TextField(blank=True, default="")
    reasoning_text = models.TextField(blank=True, default="")
    output_text = models.TextField(blank=True, default="")

    # Segmentation metadata
    segment_meta = models.JSONField(
        default=dict,
        blank=True,
        help_text="parser_version, confidence, extraction notes",
    )

    # Traceability to durable artefacts
    object_refs = models.ManyToManyField(
        "objects.KnowledgeObject",
        blank=True,
        related_name="source_messages",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence"]
        indexes = [
            models.Index(fields=["chat", "sequence"]),
            models.Index(fields=["chat", "created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["chat", "sequence"],
                name="uniq_message_sequence_per_chat",
            ),
        ]

    def save(self, *args, **kwargs):
        """
        Auto-assign sequence if not set.
        SQLite-friendly approach (good for v1). For high concurrency later (Postgres),
        you may want a stronger locking strategy.
        """
        if not self.sequence:
            with transaction.atomic():
                last = (
                    ChatMessage.objects.filter(chat_id=self.chat_id)
                    .aggregate(m=Max("sequence"))
                    .get("m")
                    or 0
                )
                self.sequence = last + 1

        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.chat_id}:{self.sequence}:{self.role}"

"""
ChatSnapshot

Purpose:
- Immutable provenance record for a chat.
- Captures which project rules governed execution at chat creation.

Stores:
- References (not content) to:
  - Level 1 identity config
  - Level 2 LLM / reasoning rules
  - Level 3 governance policy
  - Level 4 user prefs
  - Session override hash (if any)

Guarantees:
- One snapshot per chat.
- Append-only; never updated.

Used for:
- Audit
- Reproducibility
- Dispute resolution
- "Why did the model behave this way?"

Design rule:
- Projects own truth.
- Chats execute snapshots of that truth.
"""


class ChatSnapshot(models.Model):
    """
    Immutable record of which rules governed a chat.
    """

    chat = models.OneToOneField(
        "chats.ChatWorkspace",
        on_delete=models.CASCADE,
        related_name="snapshot",
    )

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="chat_snapshots",
    )

    # Config references (Levels 1-3)
    l1_ref = models.JSONField()
    l2_ref = models.JSONField()
    l3_ref = models.JSONField()

    # Level 4
    user_prefs_ref = models.JSONField()

    # Session overrides
    overrides_hash = models.CharField(max_length=64, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["chat"],
                name="uniq_snapshot_per_chat",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("ChatSnapshot is immutable.")
        return super().save(*args, **kwargs)