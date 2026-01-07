# projects/admin.py
from django.contrib import admin
from .models import Project, Folder


class FolderInline(admin.TabularInline):
    """
    Show folders under a project for quick navigation setup.
    """
    model = Folder
    extra = 0
    fields = ("name", "parent", "ordering")
    ordering = ("parent_id", "ordering", "name")
    autocomplete_fields = ("parent",)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at", "updated_at")
    list_filter = ("owner",)
    search_fields = ("name", "description", "owner__username", "owner__email")
    ordering = ("name",)
    readonly_fields = ("created_at", "updated_at")

    autocomplete_fields = ("owner",)
    inlines = (FolderInline,)


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "parent", "ordering")
    list_filter = ("project",)
    search_fields = ("name", "project__name")
    ordering = ("project__name", "parent_id", "ordering", "name")
    autocomplete_fields = ("project", "parent")
