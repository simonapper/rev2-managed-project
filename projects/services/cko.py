# projects/services/cko.py
# Purpose:
# - Governance gate for accepting a ProjectCKO as the project's definition latch.
# - Enforces: at most one ACCEPTED CKO per project.
#
# Notes:
# - Keep this service free of view/template concerns.
# - Strings/comments are 7-bit ASCII.

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any

from django.db import transaction
from django.utils import timezone

from projects.models import Project, ProjectCKO


class ProjectCKOAcceptError(Exception):
    pass


@dataclass(frozen=True)
class AcceptResult:
    project_id: int
    accepted_cko_id: int
    previous_accepted_cko_id: Optional[int]
    status: str  # "accepted" or "noop"


@transaction.atomic
def accept_project_cko(*, project: Project, cko: ProjectCKO, actor_user) -> AcceptResult:
    """
    Accept a DRAFT CKO as the canonical project definition.

    Effects (atomic):
    - Any previous ACCEPTED CKO becomes SUPERSEDED.
    - This CKO becomes ACCEPTED with accepted_by/accepted_at set.
    - Project.defined_cko becomes this CKO; defined_by/defined_at set.

    Raises:
    - ProjectCKOAcceptError for invalid inputs/state.
    """

    if project.pk is None or cko.pk is None:
        raise ProjectCKOAcceptError("Project and CKO must be saved before acceptance.")

    if cko.project_id != project.id:
        raise ProjectCKOAcceptError("CKO does not belong to this project.")

    # Lock the project row to prevent concurrent accepts.
    project_locked = Project.objects.select_for_update().get(pk=project.pk)

    # Lock the CKO row as well.
    cko_locked = ProjectCKO.objects.select_for_update().get(pk=cko.pk)

    # If already accepted and latched, treat as idempotent.
    if (
        cko_locked.status == ProjectCKO.Status.ACCEPTED
        and project_locked.defined_cko_id == cko_locked.id
    ):
        prev_id = cko_locked.id
        return AcceptResult(
            project_id=project_locked.id,
            accepted_cko_id=cko_locked.id,
            previous_accepted_cko_id=prev_id,
            status="noop",
        )

    if cko_locked.status != ProjectCKO.Status.DRAFT:
        raise ProjectCKOAcceptError("Only DRAFT CKOs can be accepted.")

    prev = (
        ProjectCKO.objects.select_for_update()
        .filter(project_id=project_locked.id, status=ProjectCKO.Status.ACCEPTED)
        .order_by("-version")
        .first()
    )

    now = timezone.now()

    # Supercede previous accepted CKO if present.
    prev_id = None
    if prev is not None:
        prev_id = prev.id
        prev.status = ProjectCKO.Status.SUPERSEDED
        prev.save(update_fields=["status"])

    # Accept this CKO.
    cko_locked.status = ProjectCKO.Status.ACCEPTED
    cko_locked.accepted_by = actor_user
    cko_locked.accepted_at = now
    cko_locked.save(update_fields=["status", "accepted_by", "accepted_at"])

    # Latch the project definition to this CKO.
    project_locked.defined_cko = cko_locked
    project_locked.defined_by = actor_user
    project_locked.defined_at = now
    project_locked.save(update_fields=["defined_cko", "defined_by", "defined_at"])

    # Audit log hook (wire to your real audit model/service).
    # Example (pseudo):
    # write_audit_log(
    #     actor=actor_user,
    #     action="PROJECT_CKO_ACCEPTED",
    #     project_id=project_locked.id,
    #     meta={"old_cko_id": prev_id, "new_cko_id": cko_locked.id},
    # )

    return AcceptResult(
        project_id=project_locked.id,
        accepted_cko_id=cko_locked.id,
        previous_accepted_cko_id=prev_id,
        status="accepted",
    )
