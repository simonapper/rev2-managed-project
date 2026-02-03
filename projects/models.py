# -*- coding: utf-8 -*-
# projects/models.py

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

from .enums import ChatReadScope
from accounts.models_avatars import Avatar


class Project(models.Model):
    """
    Top-level container.
    Everything meaningful lives inside a Project.
    """

    class Kind(models.TextChoices):
        STANDARD = "STANDARD", "Standard"
        SANDBOX = "SANDBOX", "Sandbox"

    # Work intent (orthogonal to Kind)
    class PrimaryType(models.TextChoices):
        META = "META", "Meta / Programme"
        KNOWLEDGE = "KNOWLEDGE", "Knowledge"
        DELIVERY = "DELIVERY", "Delivery"
        RESEARCH = "RESEARCH", "Research"
        OPERATIONS = "OPERATIONS", "Operations"

    class Mode(models.TextChoices):
        PLAN = "PLAN", "Plan"
        EXECUTE = "EXECUTE", "Execute"
        REVIEW = "REVIEW", "Review"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        PAUSED = "PAUSED", "Paused"
        ARCHIVED = "ARCHIVED", "Archived"

    # Identity
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)

    # Short, explicit "why this exists" statement
    purpose = models.TextField(blank=True, default="")

    # Classification
    kind = models.CharField(
        max_length=20,
        choices=Kind.choices,
        default=Kind.STANDARD,
    )
    primary_type = models.CharField(
        max_length=20,
        choices=PrimaryType.choices,
        default=PrimaryType.DELIVERY,
    )
    mode = models.CharField(
        max_length=10,
        choices=Mode.choices,
        default=Mode.PLAN,
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    # Ownership
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_projects",
    )

    # Artefact storage (authoritative project-owned space)
    artefact_root_ref = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Root path for project artefacts under MEDIA_ROOT",
    )

    # Project-level Operating Profile (Level 4)
    active_l4_config = models.ForeignKey(
        "config.ConfigRecord",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="projects_using_as_active_l4",
    )

    # Commit latch: DEFINED iff an accepted CKO exists
    defined_cko = models.ForeignKey(
        "projects.ProjectCKO",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects_where_defined",
        help_text="Points to the accepted (current) CKO for this project. Non-null iff DEFINED.",
    )
    defined_at = models.DateTimeField(null=True, blank=True)
    defined_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="projects_defined",
    )

    # Revision counter for PDE cycles (optional but useful)
    pde_revision = models.IntegerField(
        default=0,
        help_text="Increments whenever a new PDE revision cycle begins.",
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name


class ProjectPolicy(models.Model):
    """
    Project-wide constraints / defaults ("rails").
    This is the project half of Level 4 (project-wide constraints),
    plus pointers to inherited Level 1-3 configs.

    Keep this 1:1 with Project.
    """

    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name="policy")

    chat_read_scope_default = models.CharField(
        max_length=30,
        choices=ChatReadScope.choices,
        default=ChatReadScope.PROJECT_MANAGERS,
    )

    # Pointers to active project-scoped config records (Levels 1-3)
    active_l1_config = models.ForeignKey(
        "config.ConfigRecord",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="projects_using_as_active_l1",
    )
    active_l2_config = models.ForeignKey(
        "config.ConfigRecord",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="projects_using_as_active_l2",
    )
    active_l3_config = models.ForeignKey(
        "config.ConfigRecord",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="projects_using_as_active_l3",
    )

    # Policy refs (store as FK later if you model these explicitly)
    authority_model_ref = models.CharField(max_length=255, blank=True, default="")
    checkpoint_policy_ref = models.CharField(max_length=255, blank=True, default="")
    llm_policy_ref = models.CharField(max_length=255, blank=True, default="")

    # Project defaults
    language_default = models.CharField(max_length=20, default="en-GB")
    output_format_default = models.CharField(max_length=50, default="code_format")

    # Permission gates (project controls what users may override)
    user_can_override_language = models.BooleanField(default=True)
    user_can_override_checkpointing = models.BooleanField(default=True)
    user_can_override_output_format = models.BooleanField(default=True)
    user_can_override_templates = models.BooleanField(default=False)

    feature_flags = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Policy:{self.project_id}"


class UserProjectPrefs(models.Model):
    """
    Per-user preferences within a project ("driving position").
    This is the user half of Level 4 (user-wide within project).
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="user_prefs")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="project_prefs",
    )

    cognitive_avatar = models.ForeignKey(
        "accounts.Avatar",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="projectprefs_cognitive",
        limit_choices_to={"category": Avatar.Category.COGNITIVE, "is_active": True},
    )
    interaction_avatar = models.ForeignKey(
        "accounts.Avatar",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="projectprefs_interaction",
        limit_choices_to={"category": Avatar.Category.INTERACTION, "is_active": True},
    )
    presentation_avatar = models.ForeignKey(
        "accounts.Avatar",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="projectprefs_presentation",
        limit_choices_to={"category": Avatar.Category.PRESENTATION, "is_active": True},
    )
    epistemic_avatar = models.ForeignKey(
        "accounts.Avatar",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="projectprefs_epistemic",
        limit_choices_to={"category": Avatar.Category.EPISTEMIC, "is_active": True},
    )
    performance_avatar = models.ForeignKey(
        "accounts.Avatar",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="projectprefs_performance",
        limit_choices_to={"category": Avatar.Category.PERFORMANCE, "is_active": True},
    )
    checkpointing_avatar = models.ForeignKey(
        "accounts.Avatar",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="projectprefs_checkpointing",
        limit_choices_to={"category": Avatar.Category.CHECKPOINTING, "is_active": True},
    )

    # Blank means "inherit"
    active_language = models.CharField(max_length=20, blank=True, default="")
    verbosity = models.CharField(max_length=20, blank=True, default="")  # terse/standard/detailed
    tone = models.CharField(max_length=20, blank=True, default="")       # neutral/direct/coaching
    formatting = models.CharField(max_length=20, blank=True, default="") # bullets/tables/mixed

    checkpointing_override = models.CharField(max_length=20, blank=True, default="")  # inherit/strict/standard/light
    preferred_outputs = models.JSONField(default=list, blank=True)  # e.g. ["checklist", "decision_log"]

    ui_overrides = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "user"], name="uniq_userprefs_per_project"),
        ]

    def __str__(self) -> str:
        return f"Prefs:{self.project_id}:{self.user_id}"


class ProjectMembership(models.Model):
    """
    Membership and roles for a project, time-bounded and scope-aware.
    """

    chat_read_scope_override = models.CharField(
        max_length=30,
        choices=ChatReadScope.choices,
        blank=True,
        default="",  # empty = inherit project default
    )

    class Role(models.TextChoices):
        OWNER = "OWNER", "Owner"
        MANAGER = "MANAGER", "Manager"
        CONTRIBUTOR = "CONTRIBUTOR", "Contributor"
        OBSERVER = "OBSERVER", "Observer"

    class ScopeType(models.TextChoices):
        PROJECT = "PROJECT", "Project"
        MODULE = "MODULE", "Module"
        ARTEFACT = "ARTEFACT", "Artefact"
        TAG = "TAG", "Tag"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INVITED = "INVITED", "Invited"
        SUSPENDED = "SUSPENDED", "Suspended"
        LEFT = "LEFT", "Left"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="project_memberships",
    )

    role = models.CharField(max_length=20, choices=Role.choices)
    scope_type = models.CharField(max_length=20, choices=ScopeType.choices, default=ScopeType.PROJECT)
    scope_ref = models.CharField(max_length=255, blank=True, default="")  # required if scope_type != PROJECT

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    effective_from = models.DateTimeField(auto_now_add=True)
    effective_to = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["project", "user"]),
            models.Index(fields=["project", "role"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "user", "role", "scope_type", "scope_ref"],
                condition=Q(effective_to__isnull=True),
                name="uniq_active_membership_per_scope",
            ),
            models.CheckConstraint(
                condition=Q(scope_type="PROJECT", scope_ref="") | Q(scope_type__in=["MODULE", "ARTEFACT", "TAG"]),
                name="chk_project_scope_ref_blank",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.project_id}:{self.user_id}:{self.role}"


class AuditLog(models.Model):
    """
    Append-only audit stream for project-scoped events.
    """

    class Source(models.TextChoices):
        SYSTEM = "SYSTEM", "System"
        UI = "UI", "UI"
        API = "API", "API"
        MIGRATION = "MIGRATION", "Migration"
        ADMIN = "ADMIN", "Admin"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="audit_events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="audit_events",
    )

    event_type = models.CharField(max_length=50)
    entity_type = models.CharField(max_length=50)
    entity_id = models.CharField(max_length=64)

    field_changes = models.JSONField(null=True, blank=True)  # {field: {before:..., after:...}}
    summary = models.CharField(max_length=255)

    request_id = models.CharField(max_length=64, blank=True, default="")
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.SYSTEM)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["project", "created_at"]),
            models.Index(fields=["actor", "created_at"]),
            models.Index(fields=["entity_type", "entity_id"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("AuditLog is append-only.")
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.project_id}:{self.event_type}@{self.created_at.isoformat()}"


class ProjectDefinitionField(models.Model):
    """
    One PDE field under a Project.
    Stores draft value + last_validation JSON from PDE verify calls.
    """

    class Tier(models.TextChoices):
        L1_MUST = "L1-MUST", "L1-MUST"
        L1_GOOD = "L1-GOOD", "L1-GOOD"
        L1_NICE = "L1-NICE", "L1-NICE"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "DRAFT"
        PROPOSED = "PROPOSED", "PROPOSED"
        PASS_LOCKED = "PASS_LOCKED", "PASS_LOCKED"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="pde_fields",
    )

    field_key = models.CharField(max_length=200)

    tier = models.CharField(
        max_length=20,
        choices=Tier.choices,
        default=Tier.L1_MUST,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    value_text = models.TextField(blank=True, default="")
    last_validation = models.JSONField(blank=True, default=dict)

    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="pde_fields_locked",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("project", "field_key"),)
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "field_key"]),
        ]

    def __str__(self) -> str:
        return f"{self.project_id}:{self.field_key}"


class ProjectCKO(models.Model):
    """
    Versioned Canonical Knowledge Object for a Project.
    There can be many versions, but only one is the current accepted CKO
    (Project.defined_cko).
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ACCEPTED = "ACCEPTED", "Accepted"
        SUPERSEDED = "SUPERSEDED", "Superseded"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="cko_versions",
    )

    version = models.IntegerField(default=1)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    # File mirror under MEDIA_ROOT (optional but useful)
    rel_path = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Relative path under project artefact root.",
    )

    # Canonical content (DB authoritative)
    content_text = models.TextField(
        blank=True,
        default="",
        help_text="Full rendered CKO content at time of commit.",
    )

    field_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text="JSON snapshot of PDE fields at commit time.",
    )

    # Governance metadata
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="project_ckos_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="project_ckos_accepted",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["project", "version"]),
            models.Index(fields=["project", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "version"],
                name="uniq_project_cko_version",
            ),
        ]

    def __str__(self) -> str:
        return f"CKO:{self.project_id}:v{self.version}:{self.status}"
        return f"CKO:{self.project_id}:v{self.version}:{self.status}"


class ProjectPDESnapshot(models.Model):
    """
    Captures PDE field values at commit time, so the CKO is reproducible.
    Optional but recommended for revisions.
    """

    class State(models.TextChoices):
        OPEN = "OPEN", "Open"
        COMMITTED = "COMMITTED", "Committed"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="pde_snapshots",
    )

    revision = models.IntegerField(default=0)
    state = models.CharField(
        max_length=20,
        choices=State.choices,
        default=State.OPEN,
    )

    data_json = models.JSONField(default=dict, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pde_snapshots_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["project", "revision"]),
            models.Index(fields=["project", "state"]),
        ]

    def __str__(self) -> str:
        return f"PDE:{self.project_id}:r{self.revision}:{self.state}"


class Folder(models.Model):
    """
    Lightweight hierarchy for organising chats.
    This is navigational only, not semantic.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="folders")

    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )

    name = models.CharField(max_length=200)
    ordering = models.IntegerField(default=0)

    class Meta:
        unique_together = [("project", "parent", "name")]
        indexes = [
            models.Index(fields=["project", "parent", "ordering"]),
        ]

    def __str__(self) -> str:
        return f"{self.project_id}:{self.name}"
