# objects/admin.py
from django.contrib import admin
from .models import KnowledgeObject, KnowledgeObjectVersion, KnowledgeLink


class KnowledgeObjectVersionInline(admin.TabularInline):
    """
    Show versions under a KnowledgeObject.
    Keep read-only to preserve immutability at the admin layer.
    """
    model = KnowledgeObjectVersion
    extra = 0
    can_delete = False
    readonly_fields = ("version", "created_by", "created_at", "change_note", "content_text")
    ordering = ("-created_at",)


class OutgoingLinkInline(admin.TabularInline):
    """
    Show outgoing links (from_object -> to_object) under a KnowledgeObject.
    """
    model = KnowledgeLink
    fk_name = "from_object"
    extra = 0
    fields = ("link_type", "to_object", "note", "created_at")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("to_object",)
    ordering = ("-created_at",)


class IncomingLinkInline(admin.TabularInline):
    """
    Show incoming links (from_object -> to_object) under a KnowledgeObject.
    """
    model = KnowledgeLink
    fk_name = "to_object"
    extra = 0
    fields = ("link_type", "from_object", "note", "created_at")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("from_object",)
    ordering = ("-created_at",)


@admin.register(KnowledgeObject)
class KnowledgeObjectAdmin(admin.ModelAdmin):
    """
    Admin view for durable knowledge objects.
    Optimised for scan, filter, and search.
    """
    list_display = (
        "id",
        "object_type",
        "status",
        "classification",
        "title",
        "project",
        "owner",
        "official_id",
        "local_id",
        "updated_at",
    )
    list_filter = ("object_type", "status", "classification", "project")
    search_fields = (
        "title",
        "canonical_summary",
        "official_id",
        "local_id",
        "domain",
        "scope_text",
        "owner__username",
        "owner__email",
        "project__name",
    )
    ordering = ("-updated_at",)
    readonly_fields = ("created_at", "updated_at")

    autocomplete_fields = ("owner", "project")

    fieldsets = (
        ("Identity", {"fields": ("object_type", "title", "canonical_summary")}),
        ("IDs", {"fields": ("local_id", "official_id")}),
        ("Classification", {"fields": ("status", "classification")}),
        ("Context", {"fields": ("domain", "scope_text", "project", "owner")}),
        ("Audit", {"fields": ("created_at", "updated_at")}),
    )

    inlines = (KnowledgeObjectVersionInline, OutgoingLinkInline, IncomingLinkInline)


@admin.register(KnowledgeObjectVersion)
class KnowledgeObjectVersionAdmin(admin.ModelAdmin):
    """
    Admin view for immutable versions.
    """
    list_display = ("id", "obj", "version", "created_by", "created_at", "change_note")
    list_filter = ("created_at", "created_by")
    search_fields = ("obj__title", "version", "change_note", "content_text", "created_by__username")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)

    autocomplete_fields = ("obj", "created_by")


@admin.register(KnowledgeLink)
class KnowledgeLinkAdmin(admin.ModelAdmin):
    """
    Admin view for explicit links between knowledge objects.
    """
    list_display = ("id", "link_type", "from_object", "to_object", "created_at")
    list_filter = ("link_type", "created_at")
    search_fields = ("from_object__title", "to_object__title", "note")
    ordering = ("-created_at",)

    autocomplete_fields = ("from_object", "to_object")
