# navigator/admin.py
from django.contrib import admin
from .models import NavigatorRun, InvocationLog, RoutingEvent, TransferApproval


# NavigatorRun represents one governed execution context.
@admin.register(NavigatorRun)
class NavigatorRunAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "chat", "work_type", "mode", "workflow_ref", "started_at", "ended_at")
    list_filter = ("work_type", "mode")
    search_fields = ("workflow_ref", "chat__title", "project__name")
    autocomplete_fields = ("project", "chat")
    ordering = ("-started_at",)


# InvocationLog records every LLM/tool call for audit.
@admin.register(InvocationLog)
class InvocationLogAdmin(admin.ModelAdmin):
    list_display = ("run", "compartment", "backend", "created_at")
    list_filter = ("compartment", "backend")
    search_fields = ("backend", "input_hash", "output_hash")
    autocomplete_fields = ("run",)
    ordering = ("-created_at",)


# RoutingEvent shows how channels were mapped to compartments.
@admin.register(RoutingEvent)
class RoutingEventAdmin(admin.ModelAdmin):
    list_display = ("run", "channel", "routed_compartment", "policy_ref", "created_at")
    list_filter = ("channel", "routed_compartment")
    search_fields = ("policy_ref", "note")
    autocomplete_fields = ("run",)
    ordering = ("-created_at",)


# TransferApproval records explicit cross-compartment approvals.
@admin.register(TransferApproval)
class TransferApprovalAdmin(admin.ModelAdmin):
    list_display = ("run", "transfer_type", "from_compartment", "to_compartment", "classification", "approver", "created_at")
    list_filter = ("transfer_type", "classification")
    search_fields = ("rationale", "content_ref")
    autocomplete_fields = ("run", "approver", "obj")
    ordering = ("-created_at",)
