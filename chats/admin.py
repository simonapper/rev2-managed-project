# chats/admin.py
# -*- coding: utf-8 -*-

from django.contrib import admin

from .models import ChatWorkspace, ChatMessage


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    can_delete = False
    fields = (
        "sequence",
        "role",
        "created_at",
        "raw_text",
        "answer_text",
        "reasoning_text",
        "output_text",
    )
    readonly_fields = fields


@admin.register(ChatWorkspace)
class ChatWorkspaceAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "project", "created_by", "status", "updated_at")
    list_filter = ("status", "project")
    search_fields = ("title",)
    autocomplete_fields = ("project", "folder", "created_by")
    inlines = [ChatMessageInline]


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "chat", "sequence", "role", "created_at")
    list_filter = ("role", "created_at")
    search_fields = ("raw_text", "answer_text", "reasoning_text", "output_text")
    autocomplete_fields = ("chat",)
    readonly_fields = ("created_at",)
