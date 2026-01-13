# -*- coding: utf-8 -*-
# objects/services.py
# Purpose:
# Centralise object approval workflow + notifications (no rules in views/templates).

from __future__ import annotations

from typing import Iterable, List, Optional

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from accounts.models import Role, UserRole
from notifications.models import Notification
from objects.models import KnowledgeObject
from projects.models import Project
from django.db.models import Q

User = get_user_model()


class ObjectWorkflowError(Exception):
    pass


def _project_managers(project: Project) -> List[User]:
    """
    Managers for a project are:
    - explicit MANAGER roles (PROJECT scope)
    - plus the project owner (added by caller if needed)
    """
    manager_ids = list(
        UserRole.objects.filter(
            project=project,
            scope_type=UserRole.ScopeType.PROJECT,
            role__name=Role.Name.MANAGER,
        ).values_list("user_id", flat=True)
    )
    if not manager_ids:
        return []
    return list(User.objects.filter(id__in=manager_ids))


def _is_manager(project: Project, user: User) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser or user.is_staff:
        return True
    if project.owner_id == user.id:
        return True
    return UserRole.objects.filter(
        project=project,
        user=user,
        scope_type=UserRole.ScopeType.PROJECT,
        role__name=Role.Name.MANAGER,
    ).exists()


def _notify(
    *,
    recipients: Iterable[User],
    project: Optional[Project],
    type_: str,
    title: str,
    body: str,
    obj: Optional[KnowledgeObject] = None,
    link_url: str = "",
) -> None:
    rows = []
    for r in recipients:
        rows.append(
            Notification(
                recipient=r,
                project=project,
                type=type_,
                title=title,
                body=body,
                link_object=obj,
                link_url=link_url,
            )
        )
    if rows:
        Notification.objects.bulk_create(rows)


def _issue_official_id(obj: KnowledgeObject) -> str:
    """
    Prototype ID issuance: stable-ish and human-readable.
    Replace later with your canonical ID generator.
    """
    if obj.official_id:
        return obj.official_id
    return f"{obj.object_type}-{obj.pk:06d}"

# ------------------------------------------------------------
# Selects what projects and chats are available to the user
# ------------------------------------------------------------

@transaction.atomic
def submit_object_for_approval(*, obj: KnowledgeObject, actor: User) -> KnowledgeObject:
    if obj.project_id is None:
        raise ObjectWorkflowError("Object must be project-scoped to submit for approval (project is NULL).")

    project = obj.project

    # Sandbox: auto-approve (owner is manager; single-user invariant enforced elsewhere)
    if project.kind == Project.Kind.SANDBOX:
        now = timezone.now()
        obj.submitted_by = actor
        obj.submitted_at = now
        obj.approved_by = actor
        obj.approved_at = now
        obj.rejected_by = None
        obj.rejected_at = None
        obj.rejection_reason = ""
        obj.status = KnowledgeObject.Status.ACCEPTED
        obj.official_id = _issue_official_id(obj)
        obj.save(
            update_fields=[
                "submitted_by",
                "submitted_at",
                "approved_by",
                "approved_at",
                "rejected_by",
                "rejected_at",
                "rejection_reason",
                "status",
                "official_id",
                "updated_at",
            ]
        )
        return obj

    # Standard: submit -> CONTESTED + notify managers (+ owner)
    now = timezone.now()
    obj.submitted_by = actor
    obj.submitted_at = now
    obj.status = KnowledgeObject.Status.CONTESTED
    obj.save(update_fields=["submitted_by", "submitted_at", "status", "updated_at"])

    managers = _project_managers(project)
    if project.owner and project.owner not in managers:
        managers.append(project.owner)

    _notify(
        recipients=managers,
        project=project,
        type_=Notification.Type.NEEDS_APPROVAL,
        title="Needs approval",
        body=f"{obj.object_type} ‘{obj.title}’ submitted for approval.",
        obj=obj,
    )
    return obj


@transaction.atomic
def approve_object(*, obj: KnowledgeObject, actor: User) -> KnowledgeObject:
    if obj.project_id is None:
        raise ObjectWorkflowError("Object must be project-scoped to approve (project is NULL).")

    project = obj.project
    if not _is_manager(project, actor):
        raise ObjectWorkflowError("Only a project manager may approve.")

    if obj.status != KnowledgeObject.Status.CONTESTED:
        raise ObjectWorkflowError(f"Object must be CONTESTED to approve (is {obj.status}).")

    now = timezone.now()
    obj.status = KnowledgeObject.Status.ACCEPTED
    obj.approved_by = actor
    obj.approved_at = now
    obj.rejected_by = None
    obj.rejected_at = None
    obj.rejection_reason = ""
    obj.official_id = _issue_official_id(obj)
    obj.save(
        update_fields=[
            "status",
            "approved_by",
            "approved_at",
            "rejected_by",
            "rejected_at",
            "rejection_reason",
            "official_id",
            "updated_at",
        ]
    )

    _notify(
        recipients=[obj.owner],
        project=project,
        type_=Notification.Type.APPROVED,
        title="Approved",
        body=f"{obj.object_type} ‘{obj.title}’ was approved.",
        obj=obj,
    )
    return obj


@transaction.atomic
def reject_object(*, obj: KnowledgeObject, actor: User, reason: str, close: bool = False) -> KnowledgeObject:
    if obj.project_id is None:
        raise ObjectWorkflowError("Object must be project-scoped to reject (project is NULL).")

    project = obj.project
    if not _is_manager(project, actor):
        raise ObjectWorkflowError("Only a project manager may reject.")

    if obj.status != KnowledgeObject.Status.CONTESTED:
        raise ObjectWorkflowError(f"Object must be CONTESTED to reject (is {obj.status}).")

    now = timezone.now()
    obj.status = KnowledgeObject.Status.REJECTED_CLOSED if close else KnowledgeObject.Status.REJECTED_REWORK
    obj.rejected_by = actor
    obj.rejected_at = now
    obj.rejection_reason = reason.strip()
    obj.save(update_fields=["status", "rejected_by", "rejected_at", "rejection_reason", "updated_at"])

    _notify(
        recipients=[obj.owner],
        project=project,
        type_=Notification.Type.REJECTED,
        title="Rejected",
        body=f"{obj.object_type} ‘{obj.title}’ was rejected. {obj.rejection_reason}",
        obj=obj,
    )
    return obj
