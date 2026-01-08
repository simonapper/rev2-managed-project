# -*- coding: utf-8 -*-
# accounts/admin_avatars.py

from django.contrib import admin

from .models_avatars import Avatar, UserProfile


@admin.register(Avatar)
class AvatarAdmin(admin.ModelAdmin):
    list_display = ("category", "name", "key", "is_active", "updated_at")
    list_filter = ("category", "is_active")
    search_fields = ("name", "key", "description")
    ordering = ("category", "name")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "default_language",
        "default_language_variant",
        "cognitive_avatar",
        "interaction_avatar",
        "presentation_avatar",
        "epistemic_avatar",
        "performance_avatar",
        "checkpointing_avatar",
    )
    search_fields = ("user__username", "user__email")
    list_select_related = (
        "cognitive_avatar",
        "interaction_avatar",
        "presentation_avatar",
        "epistemic_avatar",
        "performance_avatar",
        "checkpointing_avatar",
    )
