# -*- coding: utf-8 -*-
# projects/services_project_membership.py
# Purpose:
# Centralise project membership + sandbox invariants (no rules in views/templates).

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction

from accounts.models import Role, UserRole
from projects.models import Project, ProjectMembership, ProjectPolicy
from django.db.models.functions import Now
from django.db.models import Q
from projects.models import Project


User = get_user_model()

class ProjectPermissionError(Exception):
    pass


class ProjectInvariantError(Exception):
    pass


@dataclass(frozen=True)
class MembershipResult:
    created: bool
    user_role: UserRole


def is_project_manager(project: Project, user: User) -> bool:
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

def accessible_projects_qs(user):
    """
    Canonical rule:
    A user may see a project if they own it or have any scoped role in it.
    """
    if not getattr(user, "is_authenticated", False):
        return Project.objects.none()

    if user.is_superuser or user.is_staff:
        return Project.objects.all()

    return Project.objects.filter(
        Q(owner=user) | Q(scoped_roles__user=user)
    ).distinct()

def can_view_project(project: Project, user: User) -> bool:
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
    owner: User,
    description: str = "",
    purpose: str = "",
    kind: str | None = None,
) -> Project:
    """
    Create a project and seed its invariants.

    Sandbox invariants:
    - Created with owner as the only member.
    """
    p = create_project(
        name=name,
        owner=owner,
        description=description,
        purpose=purpose,
        kind=kind,
)
    ensure_project_seeded(project=p)

    # If you want parity with accounts.UserRole, you can seed the owner there too.
    # Many code paths already treat owner as implicit manager, so this is optional.
    # add_user_role(project=p, user_to_add=owner, role_name=Role.Name.MANAGER, actor=owner)

    return p


@transaction.atomic
def add_user_role(*, project: Project, user_to_add: User, role_name: str, actor: User) -> MembershipResult:
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

    # Sync into ProjectMembership:
    # - Owner always has OWNER membership (seeded).
    # - For others, map MANAGER/CONTRIBUTOR/OBSERVER where possible.
    # If your Role.Name set differs, adjust this mapping.
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
def remove_user_from_project(*, project: Project, user_to_remove: User, actor: User) -> int:
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

    # Best-effort: end-date memberships rather than delete.
    ProjectMembership.objects.filter(
        project=project,
        user=user_to_remove,
        effective_to__isnull=True,
    ).update(
        status=ProjectMembership.Status.LEFT,
        effective_to=Now(),
    )

    return deleted
