# chats/models.py
from django.conf import settings
from django.db import models


class ChatWorkspace(models.Model):
    """
    Disposable conversational workspace.
    Chats are never treated as durable knowledge.
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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title


class ChatMessage(models.Model):
    """
    Individual message within a chat.
    Channel is critical for future sovereignty routing.
    """

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        SYSTEM = "system", "System"

    class Channel(models.TextChoices):
        ANSWER = "ANSWER", "Answer"
        COMMENTARY = "COMMENTARY", "Commentary"
        ANALYSIS = "ANALYSIS", "Analysis"
        REASONING = "REASONING", "Reasoning"
        SOURCES = "SOURCES", "Sources"
        META = "META", "Meta"

    chat = models.ForeignKey(
        ChatWorkspace,
        on_delete=models.CASCADE,
        related_name="messages",
    )

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
    )

    channel = models.CharField(
        max_length=20,
        choices=Channel.choices,
        default=Channel.ANSWER,
    )

    content = models.TextField()

    tool_metadata = models.JSONField(
        null=True,
        blank=True,
    )

    object_refs = models.ManyToManyField(
        "objects.KnowledgeObject",
        blank=True,
        related_name="referenced_by_messages",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.chat_id} | {self.role} | {self.created_at}"
