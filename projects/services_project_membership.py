# -*- coding: utf-8 -*-
# projects/services_project_membership.py
# Purpose:
# Centralise project membership + sandbox invariants (no rules in views/templates).

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Now

from accounts.models import Role, UserRole
from projects.models import Project, ProjectMembership, ProjectPolicy


UserModel = get_user_model()


class ProjectPermissionError(Exception):
    pass


class ProjectInvariantError(Exception):
    pass


@dataclass(frozen=True)
class MembershipResult:
    created: bool
    user_role: UserRole


def is_project_manager(project: Project, user: AbstractUser) -> bool:
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

def is_project_committer(project: Project, user: AbstractUser) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return project.owner_id == user.id

def is_project_contributor(project: Project, user: AbstractUser) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if project.owner_id == user.id:
        return True
    return ProjectMembership.objects.filter(
        project=project,
        user=user,
        status=ProjectMembership.Status.ACTIVE,
        effective_to__isnull=True,
        role__in=[ProjectMembership.Role.CONTRIBUTOR, ProjectMembership.Role.MANAGER],
    ).exists()

def can_edit_pde(project: Project, user: AbstractUser) -> bool:
    return is_project_committer(project, user) or is_project_contributor(project, user)

def can_edit_ppde(project: Project, user: AbstractUser) -> bool:
    return is_project_committer(project, user) or is_project_contributor(project, user)

def can_edit_committee(project: Project, user: AbstractUser) -> bool:
    return is_project_committer(project, user)


def accessible_projects_qs(user: AbstractUser):
    """
    Canonical rule:
    A user may see a project if they own it or have an ACTIVE membership.
    No automatic privilege for staff/superuser.
    """
    if not getattr(user, "is_authenticated", False):
        return Project.objects.none()

    return (
        Project.objects
        .filter(
            Q(owner=user)
            | Q(
                memberships__user=user,
                memberships__status=ProjectMembership.Status.ACTIVE,
                memberships__effective_to__isnull=True,
            )
        )
        .filter(status=Project.Status.ACTIVE)
        .distinct()
    )


def can_view_project(project: Project, user: AbstractUser) -> bool:
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
    ).exists()


@transaction.atomic
def ensure_project_seeded(project: Project) -> None:
    """
    Idempotently ensure:
    - ProjectPolicy exists (1:1)
    - OWNER ProjectMembership exists for project.owner
    """
    ProjectPolicy.objects.get_or_create(project=project)

    ProjectMembership.objects.get_or_create(
        project=project,
        user=project.owner,
        role=ProjectMembership.Role.OWNER,
        scope_type=ProjectMembership.ScopeType.PROJECT,
        scope_ref="",
        defaults={"status": ProjectMembership.Status.ACTIVE},
    )


@transaction.atomic
def create_project(
    *,
    name: str,
    owner: AbstractUser,
    description: str = "",
    purpose: str = "",
    kind: str | None = None,
) -> Project:
    """
    Create a project and seed its invariants.

    Sandbox invariants:
    - Created with owner as the only member.
    """
    # FIX: create Project model instance (do not call create_project recursively)
    p = Project.objects.create(
        name=name,
        owner=owner,
        description=description,
        purpose=purpose,
        kind=kind or Project.Kind.SANDBOX,
    )

    ensure_project_seeded(project=p)

    # Optional parity: seed owner as MANAGER in UserRole
    # add_user_role(project=p, user_to_add=owner, role_name=Role.Name.MANAGER, actor=owner)

    return p


@transaction.atomic
def add_user_role(*, project: Project, user_to_add: AbstractUser, role_name: str, actor: AbstractUser) -> MembershipResult:
    """
    Add or update a user's project-scoped role.

    Sandbox invariants:
    - Only the owner may be present in a sandbox project.
    - Only the owner may be assigned roles in sandbox.

    Also keeps the new ProjectMembership table in sync (best-effort).
    """
    if not is_project_manager(project, actor):
        raise ProjectPermissionError("Only a project manager (or owner) may change membership.")

    # Ensure base invariants exist before adding roles.
    ensure_project_seeded(project=project)

    if project.kind == Project.Kind.SANDBOX:
        if user_to_add.id != project.owner_id:
            raise ProjectInvariantError("Sandbox projects are single-user (owner only).")

    role = Role.objects.get(name=role_name)

    ur, created = UserRole.objects.update_or_create(
        project=project,
        user=user_to_add,
        scope_type=UserRole.ScopeType.PROJECT,
        defaults={"role": role},
    )

    role_map = {
        getattr(Role.Name, "MANAGER", "MANAGER"): ProjectMembership.Role.MANAGER,
        getattr(Role.Name, "CONTRIBUTOR", "CONTRIBUTOR"): ProjectMembership.Role.CONTRIBUTOR,
        getattr(Role.Name, "OBSERVER", "OBSERVER"): ProjectMembership.Role.OBSERVER,
    }
    pm_role = role_map.get(role_name)

    if user_to_add.id == project.owner_id:
        # Owner membership handled by seeding; leave accounts role as-is.
        pass
    elif pm_role:
        ProjectMembership.objects.update_or_create(
            project=project,
            user=user_to_add,
            role=pm_role,
            scope_type=ProjectMembership.ScopeType.PROJECT,
            scope_ref="",
            defaults={
                "status": ProjectMembership.Status.ACTIVE,
                "effective_to": None,
            },
        )

    return MembershipResult(created=created, user_role=ur)


@transaction.atomic
def remove_user_from_project(*, project: Project, user_to_remove: AbstractUser, actor: AbstractUser) -> int:
    """
    Removes all project-scoped roles for a user.

    Sandbox invariants:
    - Owner cannot be removed from their sandbox.

    Also deactivates ProjectMembership records (best-effort).
    """
    if not is_project_manager(project, actor):
        raise ProjectPermissionError("Only a project manager (or owner) may change membership.")

    ensure_project_seeded(project=project)

    if project.kind == Project.Kind.SANDBOX and user_to_remove.id == project.owner_id:
        raise ProjectInvariantError("Sandbox owner cannot be removed from their sandbox project.")

    deleted, _ = UserRole.objects.filter(
        project=project,
        user=user_to_remove,
        scope_type=UserRole.ScopeType.PROJECT,
    ).delete()

    ProjectMembership.objects.filter(
        project=project,
        user=user_to_remove,
        effective_to__isnull=True,
    ).update(
        status=ProjectMembership.Status.LEFT,
        effective_to=Now(),
    )

    return deleted
