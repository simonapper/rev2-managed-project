# -*- coding: utf-8 -*-
# config/admin.py

from django import forms
from django.contrib import admin

from .models import ConfigScope, ConfigRecord, ConfigVersion


# ============================================================
# Admin Form — ConfigRecord
# Guides valid Level ↔ Scope combinations
# ============================================================

class ConfigRecordAdminForm(forms.ModelForm):
    class Meta:
        model = ConfigRecord
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Determine level from instance (edit) or initial (add)
        level = self.initial.get("level") or getattr(self.instance, "level", None)

        if not level:
            return

        # Restrict scope choices based on level
        if level == ConfigRecord.Level.L1:
            self.fields["scope"].queryset = ConfigScope.objects.filter(
                scope_type__in=[
                    ConfigScope.ScopeType.USER,
                    ConfigScope.ScopeType.ORG,
                ]
            )

        elif level == ConfigRecord.Level.L2:
            self.fields["scope"].queryset = ConfigScope.objects.filter(
                scope_type=ConfigScope.ScopeType.ORG
            )

        elif level == ConfigRecord.Level.L3:
            self.fields["scope"].queryset = ConfigScope.objects.filter(
                scope_type=ConfigScope.ScopeType.ORG
            )

        elif level == ConfigRecord.Level.L4:
            self.fields["scope"].queryset = ConfigScope.objects.filter(
                scope_type__in=[
                    ConfigScope.ScopeType.PROJECT,
                    ConfigScope.ScopeType.SESSION,
                ]
            )


# ============================================================
# ConfigScope Admin
# ============================================================

@admin.register(ConfigScope)
class ConfigScopeAdmin(admin.ModelAdmin):
    list_display = ("scope_type", "project", "user", "session_id")
    list_filter = ("scope_type",)
    search_fields = ("project__name", "user__username", "session_id")
    autocomplete_fields = ("project", "user")


# ============================================================
# ConfigRecord Admin
# ============================================================

@admin.register(ConfigRecord)
class ConfigRecordAdmin(admin.ModelAdmin):
    form = ConfigRecordAdminForm

    list_display = ("file_id", "level", "scope", "status", "created_at")
    list_filter = ("level", "status")
    search_fields = ("file_id", "file_name")
    autocomplete_fields = ("created_by",)

    fieldsets = (
        (None, {
            "fields": ("level", "file_id", "file_name", "status"),
        }),
        ("Scope", {
            "description": (
                "Defines where this configuration applies. "
                "Valid scopes are restricted by the selected level."
            ),
            "fields": ("scope",),
        }),
        ("Audit", {
            "fields": ("created_by", "created_at"),
        }),
    )

    readonly_fields = ("created_at",)


# ============================================================
# ConfigVersion Admin
# ============================================================

@admin.register(ConfigVersion)
class ConfigVersionAdmin(admin.ModelAdmin):
    list_display = ("config", "version", "created_by", "created_at")
    list_filter = ("config",)
    search_fields = ("config__file_id", "version")
    autocomplete_fields = ("config", "created_by")

    fieldsets = (
        (None, {
            "fields": ("config", "version"),
        }),
        ("Content", {
            "fields": ("content_text",),
        }),
        ("Audit", {
            "fields": ("change_note", "created_by", "created_at"),
        }),
    )

    readonly_fields = ("created_at",)
