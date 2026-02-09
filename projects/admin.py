# -*- coding: utf-8 -*-
# projects/admin.py

from __future__ import annotations

from django import forms
from django.contrib import admin
from django.contrib import messages
from django.db import transaction
from django.utils.html import format_html

from config.models import ConfigRecord, ConfigScope
from .models import (
    AuditLog,
    Folder,
    PhaseContract,
    Project,
    ProjectMembership,
    ProjectPolicy,
    UserProjectPrefs,
)


@admin.register(PhaseContract)
class PhaseContractAdmin(admin.ModelAdmin):
    list_display = ("key", "title", "version", "is_active", "created_at", "created_by")
    list_filter = ("key", "is_active")
    search_fields = ("key", "title")
    readonly_fields = ("created_at", "created_by")
    actions = ["activate_contract_version"]

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Activate selected contract version")
    def activate_contract_version(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one contract version to activate.", level=messages.ERROR)
            return
        contract = queryset.first()
        if not contract:
            return
        with transaction.atomic():
            PhaseContract.objects.filter(key=contract.key).update(is_active=False)
            PhaseContract.objects.filter(pk=contract.pk).update(is_active=True)
        self.message_user(request, f"Activated {contract.key} v{contract.version}.", level=messages.SUCCESS)


# ------------------------------------------------------------
# Project form: constrain active_l4_config to L4 PROJECT-scoped
# ------------------------------------------------------------

class ProjectAdminForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        field = self.fields.get("active_l4_config")
        if not field:
            return

        qs = (
            ConfigRecord.objects.filter(
                level=ConfigRecord.Level.L4,
                status=ConfigRecord.Status.ACTIVE,
                scope__scope_type=ConfigScope.ScopeType.PROJECT,
            )
            .select_related("scope")
        )

        # When creating a Project, we don't know the project yet, so show none.
        if self.instance and self.instance.pk:
            qs = qs.filter(scope__project=self.instance)
        else:
            qs = qs.none()

        field.queryset = qs
        field.required = False


# ------------------------------------------------------------
# Inlines
# ------------------------------------------------------------

class ProjectPolicyInline(admin.StackedInline):
    model = ProjectPolicy
    extra = 0
    max_num = 1
    can_delete = False
    show_change_link = True
    fk_name = "project"

    autocomplete_fields = ("active_l1_config", "active_l2_config", "active_l3_config")
    readonly_fields = ("created_at", "updated_at")


class UserProjectPrefsInline(admin.TabularInline):
    model = UserProjectPrefs
    extra = 0
    show_change_link = True

    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")

    fields = (
        "user",
        "active_language",
        "verbosity",
        "tone",
        "formatting",
        "checkpointing_override",
        "preferred_outputs",
        "ui_overrides",
        "created_at",
        "updated_at",
    )


class ProjectMembershipInline(admin.TabularInline):
    model = ProjectMembership
    extra = 0
    show_change_link = True

    autocomplete_fields = ("user",)
    readonly_fields = ("effective_from", "created_at", "updated_at")

    fields = (
        "user",
        "role",
        "scope_type",
        "scope_ref",
        "status",
        "effective_from",
        "effective_to",
        "created_at",
        "updated_at",
    )


# ------------------------------------------------------------
# Model admins
# ------------------------------------------------------------

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    form = ProjectAdminForm

    list_display = (
        "name",
        "kind",
        "primary_type",
        "mode",
        "status",
        "owner",
        "active_l4_config",
        "created_at",
        "updated_at",
    )
    list_filter = ("kind", "primary_type", "mode", "status")
    search_fields = ("name", "description", "purpose", "owner__username", "owner__email")
    autocomplete_fields = ("owner", "active_l4_config")
    ordering = ("name",)
    readonly_fields = ("created_at", "updated_at")

    inlines = (ProjectPolicyInline, ProjectMembershipInline, UserProjectPrefsInline)

    fieldsets = (
        ("Identity", {"fields": ("name", "description", "purpose")}),
        ("Classification", {"fields": ("kind", "primary_type", "mode", "status")}),
        ("Ownership & Config", {"fields": ("owner", "active_l4_config")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(ProjectPolicy)
class ProjectPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "project",
        "language_default",
        "output_format_default",
        "user_can_override_language",
        "user_can_override_checkpointing",
        "user_can_override_output_format",
        "updated_at",
    )
    list_filter = (
        "user_can_override_language",
        "user_can_override_checkpointing",
        "user_can_override_output_format",
        "user_can_override_templates",
    )
    search_fields = ("project__name", "authority_model_ref", "checkpoint_policy_ref", "llm_policy_ref")
    autocomplete_fields = ("project", "active_l1_config", "active_l2_config", "active_l3_config")
    readonly_fields = ("created_at", "updated_at")


@admin.register(UserProjectPrefs)
class UserProjectPrefsAdmin(admin.ModelAdmin):
    list_display = ("project", "user", "active_language", "verbosity", "tone", "formatting", "updated_at")
    list_filter = ("verbosity", "tone", "formatting")
    search_fields = ("project__name", "user__username", "user__email")
    autocomplete_fields = ("project", "user")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ProjectMembership)
class ProjectMembershipAdmin(admin.ModelAdmin):
    list_display = (
        "project",
        "user",
        "role",
        "scope_type",
        "scope_ref",
        "status",
        "effective_from",
        "effective_to",
    )
    list_filter = ("role", "scope_type", "status")
    search_fields = ("project__name", "user__username", "user__email", "scope_ref")
    autocomplete_fields = ("project", "user")
    readonly_fields = ("effective_from", "created_at", "updated_at")
    ordering = ("project", "user", "role")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """
    AuditLog is append-only.
    Admin should allow viewing/filtering only.
    """
    list_display = (
        "created_at",
        "project",
        "actor",
        "event_type",
        "entity_type",
        "entity_id",
        "source",
        "summary",
    )
    list_filter = ("source", "event_type", "entity_type", "project")
    search_fields = ("summary", "entity_id", "actor__username", "actor__email", "project__name")
    autocomplete_fields = ("project", "actor")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    readonly_fields = (
        "project",
        "actor",
        "event_type",
        "entity_type",
        "entity_id",
        "field_changes",
        "summary",
        "request_id",
        "source",
        "created_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ("project", "parent", "name", "ordering")
    list_filter = ("project",)
    search_fields = ("name", "project__name")
    autocomplete_fields = ("project", "parent")
    ordering = ("project", "parent", "ordering", "name")
