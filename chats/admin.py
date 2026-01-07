# chats/admin.py
from django.contrib import admin
from .models import ChatWorkspace, ChatMessage


class ChatMessageInline(admin.TabularInline):
    """
    Inline messages under a chat workspace.
    Default to read-only to avoid accidental edits.
    """
    model = ChatMessage
    extra = 0
    can_delete = False
    readonly_fields = ("role", "channel", "content", "created_at", "tool_metadata")
    fields = ("created_at", "role", "channel", "content")
    ordering = ("created_at",)


@admin.register(ChatWorkspace)
class ChatWorkspaceAdmin(admin.ModelAdmin):
    """
    Admin view for chat workspaces.
    """
    list_display = ("id", "title", "project", "folder", "status", "created_by", "updated_at")
    list_filter = ("status", "project")
    search_fields = ("title", "project__name", "folder__name", "created_by__username", "created_by__email")
    ordering = ("-updated_at",)
    readonly_fields = ("created_at", "updated_at")

    autocomplete_fields = ("project", "folder", "created_by")

    inlines = (ChatMessageInline,)


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    """
    Admin view for individual messages.
    """
    list_display = ("id", "chat", "role", "channel", "created_at")
    list_filter = ("role", "channel", "created_at")
    search_fields = ("chat__title", "content")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)

    autocomplete_fields = ("chat", "object_refs")
