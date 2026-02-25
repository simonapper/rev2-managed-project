# -*- coding: utf-8 -*-
# projects/models.py

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

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

    class WorkflowMode(models.TextChoices):
        PDE = "PDE", "PDE"
        DERAX_WORK = "DERAX_WORK", "DERAX work"

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
    workflow_mode = models.CharField(
        max_length=20,
        choices=WorkflowMode.choices,
        default=WorkflowMode.PDE,
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

    # Optional PPDE seed summary (condensed CKO extract for PPDE UI)
    ppde_seed_summary = models.JSONField(default=dict, blank=True)
    boundary_profile_json = models.JSONField(default=dict, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name


class PolicyDocument(models.Model):
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="policy_documents",
    )
    title = models.CharField(max_length=200)
    body_text = models.TextField(blank=True, default="")
    source_ref = models.CharField(max_length=255, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["project", "updated_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.project_id}:{self.title}"


def project_document_upload_to(instance: "ProjectDocument", filename: str) -> str:
    base = str(filename or "document.bin")
    return f"projects/{instance.project_id}/documents/{base}"


class ProjectDocument(models.Model):
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="project_documents",
    )
    title = models.CharField(max_length=200, blank=True, default="")
    original_name = models.CharField(max_length=255, blank=True, default="")
    file = models.FileField(upload_to=project_document_upload_to)
    content_type = models.CharField(max_length=120, blank=True, default="")
    size_bytes = models.BigIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_project_documents",
    )
    wopi_lock = models.CharField(max_length=255, blank=True, default="")
    wopi_lock_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["project", "updated_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.project_id}:{self.id}:{self.original_name or self.title}"


class WorkItem(models.Model):
    PHASE_DEFINE = "DEFINE"
    PHASE_EXPLORE = "EXPLORE"
    PHASE_REFINE = "REFINE"
    PHASE_APPROVE = "APPROVE"
    PHASE_EXECUTE = "EXECUTE"
    PHASE_COMPLETE = "COMPLETE"
    ALLOWED_PHASES = (
        PHASE_DEFINE,
        PHASE_EXPLORE,
        PHASE_REFINE,
        PHASE_APPROVE,
        PHASE_EXECUTE,
        PHASE_COMPLETE,
    )

    SEED_STATUS_DRAFT = "DRAFT"
    SEED_STATUS_PROPOSED = "PROPOSED"
    SEED_STATUS_PASS_LOCKED = "PASS_LOCKED"
    SEED_STATUS_RETIRED = "RETIRED"
    SEED_STATUS_ACTIVE = "ACTIVE"  # legacy alias

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="work_items",
    )
    is_primary = models.BooleanField(default=False)
    title = models.CharField(max_length=200, blank=True, default="")
    intent_raw = models.TextField(blank=True, default="")
    state = models.CharField(max_length=40, default="NEW")
    active_phase = models.CharField(max_length=80, blank=True, default="")
    seed_log = models.JSONField(default=list, blank=True)
    active_seed_revision = models.PositiveIntegerField(default=0)
    deliverables = models.JSONField(default=list, blank=True)
    activity_log = models.JSONField(default=list, blank=True)
    boundary_profile_json = models.JSONField(default=dict, blank=True)
    derax_endpoint_spec = models.TextField(blank=True, default="")
    derax_endpoint_locked = models.BooleanField(default=False)
    derax_define_history = models.JSONField(default=list, blank=True)
    derax_explore_history = models.JSONField(default=list, blank=True)
    derax_runs = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["project", "updated_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["project"],
                condition=Q(is_primary=True),
                name="uq_workitem_primary_per_project",
            ),
        ]

    @classmethod
    def create_minimal(
        cls,
        *,
        project: Project,
        state: str = "NEW",
        active_phase: str = "",
        title: str = "",
        intent_raw: str = "",
    ) -> "WorkItem":
        phase = str(active_phase or cls.PHASE_DEFINE).strip().upper()
        if phase not in cls.ALLOWED_PHASES:
            raise ValueError("Invalid active phase.")
        return cls.objects.create(
            project=project,
            title=(title or "")[:200],
            intent_raw=(intent_raw or ""),
            state=(state or "NEW")[:40],
            active_phase=phase[:80],
            seed_log=[],
            active_seed_revision=0,
            deliverables=[],
            activity_log=[],
            boundary_profile_json={},
            derax_endpoint_spec="",
            derax_endpoint_locked=False,
            derax_define_history=[],
            derax_explore_history=[],
            derax_runs=[],
        )

    @staticmethod
    def _normalise_phase(raw_phase: str) -> str:
        return str(raw_phase or "").strip().upper()

    def _current_phase(self) -> str:
        phase = self._normalise_phase(self.active_phase) or self.PHASE_DEFINE
        if phase not in self.ALLOWED_PHASES:
            raise ValueError("Current active_phase is invalid.")
        return phase

    def _has_pass_locked_seed(self) -> bool:
        log = self._validated_seed_log()
        for item in log:
            if str(item.get("status") or "") == self.SEED_STATUS_PASS_LOCKED:
                return True
        return False

    def _has_proposed_seed_revision(self) -> bool:
        log = self._validated_seed_log()
        for item in log:
            status = str(item.get("status") or "").strip().upper()
            if status in {
                self.SEED_STATUS_PROPOSED,
                self.SEED_STATUS_PASS_LOCKED,
                self.SEED_STATUS_RETIRED,
                self.SEED_STATUS_ACTIVE,  # legacy
            }:
                return True
        return False

    def _has_execute_deliverables(self) -> bool:
        items = list(self.deliverables or [])
        return any(str(v or "").strip() for v in items)

    def _requires_derax_endpoint(self) -> bool:
        project = getattr(self, "project", None)
        mode = str(getattr(project, "workflow_mode", "") or "").strip().upper()
        return mode == str(Project.WorkflowMode.DERAX_WORK).strip().upper()

    def _has_locked_derax_endpoint(self) -> bool:
        return bool(self.derax_endpoint_locked and str(self.derax_endpoint_spec or "").strip())

    def evaluate_phase_transition(self, to_phase: str) -> tuple[bool, str]:
        target = self._normalise_phase(to_phase)
        if target not in self.ALLOWED_PHASES:
            return False, "Target phase is invalid."
        try:
            current = self._current_phase()
        except ValueError:
            return False, "Current phase is invalid."

        phase_order = list(self.ALLOWED_PHASES)
        current_idx = phase_order.index(current)
        target_idx = phase_order.index(target)
        if target_idx < current_idx:
            return False, "Backward phase transitions are not allowed."

        if target == self.PHASE_APPROVE and not self._has_proposed_seed_revision():
            return False, "APPROVE requires a proposed seed revision."

        if target == self.PHASE_EXECUTE and not self._has_pass_locked_seed():
            return False, "EXECUTE requires a PASS_LOCKED seed revision."
        if target == self.PHASE_EXECUTE and self._requires_derax_endpoint() and not self._has_locked_derax_endpoint():
            return False, "EXECUTE requires a locked DERAX endpoint specification."

        if target == self.PHASE_COMPLETE and current != self.PHASE_EXECUTE:
            return False, "COMPLETE requires current phase EXECUTE."
        if target == self.PHASE_COMPLETE and not self._has_execute_deliverables():
            return False, "COMPLETE requires EXECUTE deliverables."

        return True, ""

    def can_transition(self, to_phase: str) -> bool:
        ok, _reason = self.evaluate_phase_transition(to_phase)
        return bool(ok)

    def set_phase(self, new_phase: str) -> str:
        before = self._current_phase()
        target = self._normalise_phase(new_phase)
        if target not in self.ALLOWED_PHASES:
            raise ValueError("Invalid phase.")
        ok, reason = self.evaluate_phase_transition(target)
        if not ok:
            raise ValueError(reason or "Illegal phase transition.")
        self.active_phase = target
        self.save(update_fields=["active_phase", "updated_at"])
        if before != target:
            self.append_activity(
                actor="system",
                action="phase_changed",
                notes=f"{before} -> {target}",
            )
        return self.active_phase

    def set_derax_endpoint(self, spec_text: str, *, actor="user", lock: bool = False) -> None:
        text = str(spec_text or "").strip()
        self.derax_endpoint_spec = text
        if lock:
            if not text:
                raise ValueError("Cannot lock an empty DERAX endpoint specification.")
            self.derax_endpoint_locked = True
            self.save(update_fields=["derax_endpoint_spec", "derax_endpoint_locked", "updated_at"])
            self.append_activity(
                actor=actor,
                action="derax_endpoint_locked",
                notes="DERAX endpoint specification locked.",
            )
            return
        self.derax_endpoint_locked = False
        self.save(update_fields=["derax_endpoint_spec", "derax_endpoint_locked", "updated_at"])
        self.append_activity(
            actor=actor,
            action="derax_endpoint_saved",
            notes="DERAX endpoint specification updated.",
        )

    def lock_derax_endpoint(self, *, actor="user") -> None:
        text = str(self.derax_endpoint_spec or "").strip()
        if not text:
            raise ValueError("Cannot lock an empty DERAX endpoint specification.")
        self.derax_endpoint_locked = True
        self.save(update_fields=["derax_endpoint_locked", "updated_at"])
        self.append_activity(
            actor=actor,
            action="derax_endpoint_locked",
            notes="DERAX endpoint specification locked.",
        )

    def append_activity(self, *, actor, action: str, notes: str = "") -> int:
        actor_value = str(getattr(actor, "id", actor) or "system").strip().lower()
        if actor_value.isdigit():
            actor_value = "user"
        if actor_value not in {"user", "llm", "system"}:
            actor_value = "system"

        log = list(self.activity_log or [])
        log.append(
            {
                "timestamp": timezone.now().isoformat(),
                "actor": actor_value,
                "action": str(action or "").strip(),
                "notes": str(notes or "").strip(),
            }
        )
        self.activity_log = log
        self.save(update_fields=["activity_log", "updated_at"])
        return len(log)

    def add_deliverable(self, ref: str, note: str | None = None, actor: str = "system") -> int:
        ref_text = str(ref or "").strip()
        if not ref_text:
            raise ValueError("Deliverable ref is required.")
        note_text = str(note or "").strip()
        line = ref_text if not note_text else f"{ref_text} | {note_text}"
        items = list(self.deliverables or [])
        items.append(line)
        self.deliverables = items
        self.save(update_fields=["deliverables", "updated_at"])
        self.append_activity(
            actor=actor,
            action="deliverable_generated",
            notes=line,
        )
        return len(items)

    @staticmethod
    def _actor_id(created_by) -> int | None:
        if created_by is None:
            return None
        actor_id = getattr(created_by, "id", created_by)
        try:
            return int(actor_id)
        except (TypeError, ValueError):
            raise ValueError("created_by must be a user or integer id.")

    def _validated_seed_log(self) -> list[dict]:
        log = list(self.seed_log or [])
        expected_revision = 1
        pass_locked_count = 0
        for item in log:
            if not isinstance(item, dict):
                raise ValueError("seed_log entries must be dict objects.")
            revision = item.get("revision")
            if not isinstance(revision, int):
                raise ValueError("seed_log revision must be an integer.")
            if revision != expected_revision:
                raise ValueError("seed_log revisions must increase by exactly 1.")
            if str(item.get("status") or "") == self.SEED_STATUS_PASS_LOCKED:
                pass_locked_count += 1
            expected_revision += 1
        if pass_locked_count > 1:
            raise ValueError("Only one PASS_LOCKED seed revision is allowed.")
        if self.active_seed_revision < 0:
            raise ValueError("active_seed_revision cannot be negative.")
        if self.active_seed_revision > len(log):
            raise ValueError("active_seed_revision cannot exceed seed_log length.")
        return log

    def append_seed_revision(self, seed_text: str, created_by, reason: str) -> int:
        log = self._validated_seed_log()
        next_revision = len(log) + 1
        entry = {
            "revision": next_revision,
            "status": self.SEED_STATUS_PROPOSED,
            "seed_text": str(seed_text or ""),
            "created_by_id": self._actor_id(created_by),
            "reason": str(reason or ""),
            "event": "APPEND",
            "created_at": timezone.now().isoformat(),
        }
        log.append(entry)
        self.seed_log = log
        self.active_seed_revision = next_revision
        self._validated_seed_log()
        self.save(update_fields=["seed_log", "active_seed_revision", "updated_at"])
        self.append_activity(
            actor=created_by,
            action="seed_proposed",
            notes=f"revision={next_revision}; reason={str(reason or '').strip()}",
        )
        return self.active_seed_revision

    def lock_seed(self, revision_number: int) -> int:
        log = self._validated_seed_log()
        try:
            revision_number = int(revision_number)
        except (TypeError, ValueError):
            raise ValueError("revision_number must be an integer.")
        if revision_number < 1 or revision_number > len(log):
            raise ValueError("revision_number is out of range.")

        for item in log:
            if str(item.get("status") or "") == self.SEED_STATUS_PASS_LOCKED and item.get("revision") != revision_number:
                item["status"] = self.SEED_STATUS_RETIRED

        target = log[revision_number - 1]
        target["status"] = self.SEED_STATUS_PASS_LOCKED
        target["locked_at"] = timezone.now().isoformat()

        self.seed_log = log
        self.active_seed_revision = revision_number
        self._validated_seed_log()
        self.save(update_fields=["seed_log", "active_seed_revision", "updated_at"])
        self.append_activity(
            actor="user",
            action="seed_locked",
            notes=f"revision={revision_number}",
        )
        return self.active_seed_revision

    def rollback_to(self, revision_number: int) -> int:
        log = self._validated_seed_log()
        try:
            revision_number = int(revision_number)
        except (TypeError, ValueError):
            raise ValueError("revision_number must be an integer.")
        if revision_number < 1 or revision_number > len(log):
            raise ValueError("revision_number is out of range.")

        target = log[revision_number - 1]
        next_revision = len(log) + 1
        rollback_entry = {
            "revision": next_revision,
            "status": self.SEED_STATUS_PROPOSED,
            "seed_text": str(target.get("seed_text") or ""),
            "created_by_id": None,
            "reason": f"ROLLBACK_TO:{revision_number}",
            "event": "ROLLBACK",
            "rollback_to_revision": revision_number,
            "created_at": timezone.now().isoformat(),
        }
        log.append(rollback_entry)
        self.seed_log = log
        self.active_seed_revision = next_revision
        self._validated_seed_log()
        self.save(update_fields=["seed_log", "active_seed_revision", "updated_at"])
        self.append_activity(
            actor="user",
            action="seed_rollback",
            notes=f"to_revision={revision_number}; new_revision={next_revision}",
        )
        return self.active_seed_revision

    def append_seed_entry(self, entry: dict) -> int:
        payload = dict(entry or {})
        return self.append_seed_revision(
            seed_text=str(payload.get("seed_text") or ""),
            created_by=payload.get("created_by") or payload.get("created_by_id"),
            reason=str(payload.get("reason") or ""),
        )

    def __str__(self) -> str:
        return f"{self.project_id}:{self.id}:{self.state}"


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

    class PlanningMode(models.TextChoices):
        ASSISTED = "ASSISTED", "Assisted"
        AUTO = "AUTO", "Auto"

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
    planning_mode = models.CharField(
        max_length=16,
        choices=PlanningMode.choices,
        default=PlanningMode.ASSISTED,
    )

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

    proposed_at = models.DateTimeField(null=True, blank=True)
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="pde_fields_proposed",
    )

    last_edited_at = models.DateTimeField(null=True, blank=True)
    last_edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="pde_fields_edited",
    )

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

class ProjectTopicChat(models.Model):
    """
    Stable binding for one topic chat per (project, user, scope, topic_key).
    """

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="topic_chats",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="topic_chats",
    )
    scope = models.CharField(max_length=16, db_index=True)
    topic_key = models.CharField(max_length=200, db_index=True)
    chat = models.OneToOneField(
        "chats.ChatWorkspace",
        on_delete=models.CASCADE,
        related_name="topic_binding",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "user", "scope", "topic_key"],
                name="uq_project_topic_chat",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "user"]),
            models.Index(fields=["project", "scope", "topic_key"]),
        ]

    def __str__(self) -> str:
        return f"{self.project_id}:{self.user_id}:{self.scope}:{self.topic_key}"

class ProjectAnchor(models.Model):
    class Marker(models.TextChoices):
        INTENT = "INTENT", "Intent"
        ROUTE = "ROUTE", "Route"
        EXECUTE = "EXECUTE", "Execute"
        COMPLETE = "COMPLETE", "Complete"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PROPOSED = "PROPOSED", "Proposed"
        PASS_LOCKED = "PASS_LOCKED", "Locked"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="anchors",
    )
    marker = models.CharField(max_length=16, choices=Marker.choices)
    content = models.TextField(blank=True, default="")
    content_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="anchor_proposals",
    )
    proposed_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="anchor_locks",
    )
    locked_at = models.DateTimeField(null=True, blank=True)
    last_edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="anchor_edits",
    )
    last_edited_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "marker"], name="uq_project_anchor"),
        ]
        indexes = [
            models.Index(fields=["project", "marker"]),
        ]

    def __str__(self) -> str:
        return f"{self.project_id}:{self.marker}:{self.status}"


class ProjectAnchorAudit(models.Model):
    class ChangeType(models.TextChoices):
        UPDATE = "UPDATE", "Update"
        STATUS = "STATUS", "Status"
        RESEED = "RESEED", "Reseed"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="anchor_audits",
    )
    anchor = models.ForeignKey(
        "projects.ProjectAnchor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_rows",
    )
    marker = models.CharField(max_length=16, choices=ProjectAnchor.Marker.choices)
    change_type = models.CharField(max_length=16, choices=ChangeType.choices, default=ChangeType.UPDATE)
    summary = models.CharField(max_length=255, blank=True, default="")

    status_before = models.CharField(max_length=16, blank=True, default="")
    status_after = models.CharField(max_length=16, blank=True, default="")

    before_content = models.TextField(blank=True, default="")
    after_content = models.TextField(blank=True, default="")
    before_content_json = models.JSONField(default=dict, blank=True)
    after_content_json = models.JSONField(default=dict, blank=True)

    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="project_anchor_audits",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["project", "marker", "created_at"]),
            models.Index(fields=["project", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.project_id}:{self.marker}:{self.change_type}@{self.created_at.isoformat()}"


class ProjectReviewChat(models.Model):
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="review_chats",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="review_chats",
    )
    marker = models.CharField(max_length=16)
    chat = models.OneToOneField(
        "chats.ChatWorkspace",
        on_delete=models.CASCADE,
        related_name="review_binding",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "user", "marker"], name="uq_project_review_chat"),
        ]
        indexes = [
            models.Index(fields=["project", "user", "marker"]),
        ]

    def __str__(self) -> str:
        return f"{self.project_id}:{self.user_id}:{self.marker}"

class ProjectReviewStageChat(models.Model):
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="review_stage_chats",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="review_stage_chats",
    )
    marker = models.CharField(max_length=16)
    stage_number = models.IntegerField()
    chat = models.OneToOneField(
        "chats.ChatWorkspace",
        on_delete=models.CASCADE,
        related_name="review_stage_binding",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "user", "marker", "stage_number"],
                name="uq_project_review_stage_chat",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "user", "marker"], name="ix_review_stage_p_u_m"),
            models.Index(fields=["project", "marker", "stage_number"], name="ix_review_stage_p_m_s"),
        ]

    def __str__(self) -> str:
        return f"{self.project_id}:{self.user_id}:{self.marker}:S{self.stage_number}"

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
    content_html = models.TextField(
        blank=True,
        default="",
        help_text="Canonical rendered CKO HTML document.",
    )

    content_text = models.TextField(
        blank=True,
        default="",
        help_text="Plain text shadow of the CKO (export/diff/search).",
    )

    content_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Structured JSON view of the CKO.",
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


class ProjectTKO(models.Model):
    """
    Versioned Transfer Knowledge Object for a Project.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ACCEPTED = "ACCEPTED", "Accepted"
        SUPERSEDED = "SUPERSEDED", "Superseded"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="tko_versions",
    )
    version = models.IntegerField(default=1)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    content_text = models.TextField(blank=True, default="")
    content_json = models.JSONField(default=dict, blank=True)
    content_html = models.TextField(blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="project_tkos_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="project_tkos_accepted",
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
                name="uniq_project_tko_version",
            ),
        ]

    def __str__(self) -> str:
        return f"TKO:{self.project_id}:v{self.version}:{self.status}"


class ProjectPKO(models.Model):
    """
    Versioned Policy Knowledge Object for a Project.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ACCEPTED = "ACCEPTED", "Accepted"
        SUPERSEDED = "SUPERSEDED", "Superseded"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="pko_versions",
    )
    version = models.IntegerField(default=1)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    content_text = models.TextField(blank=True, default="")
    content_json = models.JSONField(default=dict, blank=True)
    content_html = models.TextField(blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="project_pkos_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="project_pkos_accepted",
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
                name="uniq_project_pko_version",
            ),
        ]

    def __str__(self) -> str:
        return f"PKO:{self.project_id}:v{self.version}:{self.status}"


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


class ProjectPlanningPurpose(models.Model):
    """
    PPDE planning purpose block for a Project.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "DRAFT"
        PROPOSED = "PROPOSED", "PROPOSED"
        PASS_LOCKED = "PASS_LOCKED", "PASS_LOCKED"

    project = models.OneToOneField(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="ppde_purpose",
    )

    value_text = models.TextField(blank=True, default="")
    pdo_summary = models.TextField(blank=True, default="")
    planning_constraints = models.TextField(blank=True, default="")
    assumptions = models.TextField(blank=True, default="")
    cko_alignment_stage1_inputs_match = models.TextField(blank=True, default="")
    cko_alignment_final_outputs_match = models.TextField(blank=True, default="")
    last_validation = models.JSONField(blank=True, default=dict)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    proposed_at = models.DateTimeField(null=True, blank=True)
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ppde_purpose_proposed",
    )

    last_edited_at = models.DateTimeField(null=True, blank=True)
    last_edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ppde_purpose_edited",
    )

    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ppde_purpose_locked",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["project", "status"]),
        ]

    def __str__(self) -> str:
        return f"PPDE:purpose:{self.project_id}"


class ProjectPlanningStage(models.Model):
    """
    PPDE planning stage block for a Project.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "DRAFT"
        PROPOSED = "PROPOSED", "PROPOSED"
        PASS_LOCKED = "PASS_LOCKED", "PASS_LOCKED"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="ppde_stages",
    )

    order_index = models.IntegerField(default=1)

    title = models.CharField(max_length=200, blank=True, default="")
    purpose = models.TextField(blank=True, default="")
    inputs = models.TextField(blank=True, default="")
    stage_process = models.TextField(blank=True, default="")
    outputs = models.TextField(blank=True, default="")
    assumptions = models.TextField(blank=True, default="")
    duration_estimate = models.TextField(blank=True, default="")
    risks_notes = models.TextField(blank=True, default="")
    last_validation = models.JSONField(blank=True, default=dict)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    proposed_at = models.DateTimeField(null=True, blank=True)
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ppde_stages_proposed",
    )

    last_edited_at = models.DateTimeField(null=True, blank=True)
    last_edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ppde_stages_edited",
    )

    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ppde_stages_locked",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "order_index"]),
        ]
        ordering = ["order_index", "id"]

    def __str__(self) -> str:
        return f"PPDE:stage:{self.project_id}:{self.id}"


class ProjectPDO(models.Model):
    """
    Versioned Planning Direction Object (PDO) for a Project (PPDE output).
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ACTIVE = "ACTIVE", "Active"
        SUPERSEDED = "SUPERSEDED", "Superseded"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="pdo_versions",
    )

    version = models.IntegerField(default=1)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    content_json = models.JSONField(default=dict, blank=True)
    seed_snapshot = models.JSONField(default=dict, blank=True)
    change_summary = models.CharField(max_length=300, blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="project_pdos_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["project", "version"]),
            models.Index(fields=["project", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "version"],
                name="uniq_project_pdo_version",
            ),
        ]

    def __str__(self) -> str:
        return f"PDO:{self.project_id}:v{self.version}:{self.status}"


class ProjectWKO(models.Model):
    """
    Versioned Workflow Knowledge Object for a Project (PPDE commit output).
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ACTIVE = "ACTIVE", "Active"
        SUPERSEDED = "SUPERSEDED", "Superseded"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="wko_versions",
    )

    version = models.IntegerField(default=1)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    structure_contract_key = models.CharField(max_length=64, blank=True, default="")
    structure_contract_version = models.IntegerField(null=True, blank=True)
    transform_contract_key = models.CharField(max_length=64, blank=True, default="")
    transform_contract_version = models.IntegerField(null=True, blank=True)

    content_json = models.JSONField(default=dict, blank=True)
    seed_snapshot = models.JSONField(default=dict, blank=True)
    change_summary = models.CharField(max_length=300, blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="project_wkos_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="project_wkos_activated",
    )
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["project", "version"]),
            models.Index(fields=["project", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "version"],
                name="uniq_project_wko_version",
            ),
        ]

    def __str__(self) -> str:
        return f"WKO:{self.project_id}:v{self.version}:{self.status}"


class ProjectPlanningMilestone(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "DRAFT"
        PROPOSED = "PROPOSED", "PROPOSED"
        PASS_LOCKED = "PASS_LOCKED", "PASS_LOCKED"

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="ppde_milestones")
    stage = models.ForeignKey("projects.ProjectPlanningStage", null=True, blank=True, on_delete=models.SET_NULL, related_name="milestones")
    order_index = models.IntegerField(default=1)
    title = models.CharField(max_length=200, blank=True, default="")
    stage_title = models.CharField(max_length=200, blank=True, default="")
    acceptance_statement = models.TextField(blank=True, default="")
    target_date_hint = models.CharField(max_length=100, blank=True, default="")

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    proposed_at = models.DateTimeField(null=True, blank=True)
    proposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="ppde_milestones_proposed")
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="ppde_milestones_locked")
    last_edited_at = models.DateTimeField(null=True, blank=True)
    last_edited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="ppde_milestones_edited")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "order_index"]),
        ]
        ordering = ["order_index", "id"]


class ProjectPlanningAction(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "DRAFT"
        PROPOSED = "PROPOSED", "PROPOSED"
        PASS_LOCKED = "PASS_LOCKED", "PASS_LOCKED"

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="ppde_actions")
    stage = models.ForeignKey("projects.ProjectPlanningStage", null=True, blank=True, on_delete=models.SET_NULL, related_name="actions")
    order_index = models.IntegerField(default=1)
    title = models.CharField(max_length=200, blank=True, default="")
    stage_title = models.CharField(max_length=200, blank=True, default="")
    owner_role = models.CharField(max_length=120, blank=True, default="")
    definition_of_done = models.TextField(blank=True, default="")
    effort_hint = models.CharField(max_length=120, blank=True, default="")

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    proposed_at = models.DateTimeField(null=True, blank=True)
    proposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="ppde_actions_proposed")
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="ppde_actions_locked")
    last_edited_at = models.DateTimeField(null=True, blank=True)
    last_edited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="ppde_actions_edited")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "order_index"]),
        ]
        ordering = ["order_index", "id"]


class ProjectPlanningRisk(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "DRAFT"
        PROPOSED = "PROPOSED", "PROPOSED"
        PASS_LOCKED = "PASS_LOCKED", "PASS_LOCKED"

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="ppde_risks")
    stage = models.ForeignKey("projects.ProjectPlanningStage", null=True, blank=True, on_delete=models.SET_NULL, related_name="risks")
    order_index = models.IntegerField(default=1)
    title = models.CharField(max_length=200, blank=True, default="")
    stage_title = models.CharField(max_length=200, blank=True, default="")
    probability = models.CharField(max_length=10, blank=True, default="")
    impact = models.CharField(max_length=10, blank=True, default="")
    mitigation = models.TextField(blank=True, default="")

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    proposed_at = models.DateTimeField(null=True, blank=True)
    proposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="ppde_risks_proposed")
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="ppde_risks_locked")
    last_edited_at = models.DateTimeField(null=True, blank=True)
    last_edited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="ppde_risks_edited")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "order_index"]),
        ]
        ordering = ["order_index", "id"]


class ProjectExecutionTask(models.Model):
    class Status(models.TextChoices):
        TODO = "TODO", "TODO"
        IN_PROGRESS = "IN_PROGRESS", "IN_PROGRESS"
        BLOCKED = "BLOCKED", "BLOCKED"
        DONE = "DONE", "DONE"

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="execution_tasks")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    stage_title = models.CharField(max_length=200, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TODO)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="execution_tasks",
    )
    due_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    source_wko_version = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["project", "stage_title"]),
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "source_wko_version"]),
        ]
class PhaseContract(models.Model):
    """
    Versioned, admin-editable contract for PPDE phase operations.
    """

    key = models.CharField(max_length=64)
    title = models.CharField(max_length=200)
    version = models.IntegerField(default=1)
    is_active = models.BooleanField(default=False)

    purpose_text = models.TextField(blank=True, default="")
    inputs_text = models.TextField(blank=True, default="")
    outputs_text = models.TextField(blank=True, default="")
    method_guidance_text = models.TextField(blank=True, default="")
    acceptance_test_text = models.TextField(blank=True, default="")

    llm_review_prompt_text = models.TextField(blank=True, default="")
    policy_json = models.JSONField(blank=True, default=dict)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="phase_contracts_created",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["key", "version"],
                name="uq_phasecontract_key_version",
            ),
        ]
        indexes = [
            models.Index(fields=["key", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"Contract:{self.key}:v{self.version}"

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

