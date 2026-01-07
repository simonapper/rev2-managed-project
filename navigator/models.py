# navigator/models.py
from django.conf import settings
from django.db import models


class NavigatorRun(models.Model):
    """
    Represents one governed execution context ("run") for a chat.

    A run is the unit of orchestration/audit:
    - ties together the selected Work Type, Mode, and Workflow reference
    - anchors routing decisions, invocations, and approvals
    - provides a stable handle for compliance logging

    Note:
    v1 can operate in single-compartment mode (COMP-PRIMARY only),
    but we still log routing/invocations to preserve the upgrade path.
    """
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="navigator_runs")
    chat = models.ForeignKey("chats.ChatWorkspace", on_delete=models.CASCADE, related_name="navigator_runs")

    # Human-facing selectors (as chosen in the UI control panel)
    work_type = models.CharField(max_length=50)       # e.g. "Engineering / Build"
    mode = models.CharField(max_length=50)            # e.g. "Engineering assistant"

    # Reference to an accepted WKO (store the ID string, not FK, to allow global WKOs)
    workflow_ref = models.CharField(max_length=120, blank=True)  # e.g. "WKO-ENG-001" or draft id

    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Run {self.id} ({self.work_type})"


class InvocationLog(models.Model):
    """
    Records every external invocation that is relevant to audit:
    - LLM calls
    - tool calls (optional to unify here, or use tool_metadata elsewhere)

    In v1 we may not split compartments, but we still record the field
    so compartmentalisation can be turned on later without schema changes.
    """
    class Compartment(models.TextChoices):
        PRIMARY = "COMP-PRIMARY", "Primary"
        ANALYSIS = "COMP-ANALYSIS", "Analysis"
        REASONING = "COMP-REASONING", "Reasoning"
        SUMMARY = "COMP-SUMMARY", "Summary"

    run = models.ForeignKey(NavigatorRun, on_delete=models.CASCADE, related_name="invocations")

    # Where the invocation was executed under the sovereignty model.
    compartment = models.CharField(max_length=20, choices=Compartment.choices, default=Compartment.PRIMARY)

    # Which backend executed it (abstract label; maps to an allow-list in Level 3 controls)
    backend = models.CharField(max_length=50, blank=True)  # e.g. "LLM-A" / "LLM-B" / "LLM-C"

    # Optional integrity hooks:
    # Store hashes of the input/output payloads (not the payloads themselves).
    # Useful for tamper-evident logging without sensitive content duplication.
    input_hash = models.CharField(max_length=64, blank=True)
    output_hash = models.CharField(max_length=64, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["run", "created_at"]),
            models.Index(fields=["compartment"]),
            models.Index(fields=["backend"]),
        ]


class RoutingEvent(models.Model):
    """
    Records how content was routed.

    Example:
    - channel=ANALYSIS -> COMP-ANALYSIS
    - channel=ANSWER -> COMP-PRIMARY

    In v1 (single-compartment mode), routing may always land in COMP-PRIMARY,
    but we still log the intended routing to preserve policy visibility and
    allow later enforcement.
    """
    run = models.ForeignKey(NavigatorRun, on_delete=models.CASCADE, related_name="routing_events")

    # Logical output channel (your Level 3 channel contract)
    channel = models.CharField(max_length=20)  # e.g. "ANSWER" / "ANALYSIS" / "REASONING" / ...

    # Physical compartment route decision
    routed_compartment = models.CharField(max_length=20)  # e.g. "COMP-PRIMARY"

    # Which control file justified this route (for audit)
    policy_ref = models.CharField(max_length=120, blank=True)  # e.g. "L3-SOV-ROUTE-001"

    # Free text note (e.g. "missing channel -> defaulted UNCLASSIFIED")
    note = models.CharField(max_length=500, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["run", "created_at"]),
            models.Index(fields=["channel"]),
            models.Index(fields=["routed_compartment"]),
        ]


class TransferApproval(models.Model):
    """
    Captures explicit approvals for cross-compartment transfer.

    Your Level 3 policy defines:
    - VIEW: allowed
    - IMPORT/EXPORT: requires approval
    - MERGE: forbidden (and should never appear here)

    Even in v1 (single-compartment), we keep this table so the approval
    workflow and audit trail exist from day one.
    """
    class TransferType(models.TextChoices):
        VIEW = "VIEW", "View"
        IMPORT = "IMPORT", "Import"
        EXPORT = "EXPORT", "Export"

    run = models.ForeignKey(
        NavigatorRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transfer_approvals",
    )

    from_compartment = models.CharField(max_length=20)  # e.g. "COMP-ANALYSIS"
    to_compartment = models.CharField(max_length=20)    # e.g. "COMP-PRIMARY"
    transfer_type = models.CharField(max_length=10, choices=TransferType.choices)

    # Data classification at time of transfer request (per Level 3 allowed classes)
    classification = models.CharField(max_length=20, default="INTERNAL")

    # Optional linkage to a specific durable object being transferred
    obj = models.ForeignKey("objects.KnowledgeObject", on_delete=models.SET_NULL, null=True, blank=True)

    # Optional pointer to some other content (e.g. version id, blob id)
    content_ref = models.CharField(max_length=200, blank=True)

    # Who approved the transfer and why (hard requirement in policy)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="transfer_approvals")
    rationale = models.CharField(max_length=500)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["run", "created_at"]),
            models.Index(fields=["transfer_type"]),
            models.Index(fields=["classification"]),
        ]
