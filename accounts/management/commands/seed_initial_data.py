# -*- coding: utf-8 -*-
# accounts/management/commands/seed_initial_data.py
# Purpose:
# Seed minimal, safe initial data for local development and prototypes.
# Notes:
# - Idempotent (safe to run multiple times)
# - Conservative (no destructive actions)
# - Explicit (no hidden side effects)

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from accounts.models import Role, UserRole
from objects.models import KnowledgeObject, KnowledgeObjectVersion
from projects.models import Project
from projects.services_project_membership import create_project, ensure_project_seeded, accessible_projects_qs


class Command(BaseCommand):
    """
    Seeds:
    1. Core roles (ADMIN / MANAGER / USER)
    2. A local admin user (dev only)
    3. ORG-scoped ADMIN UserRole assignment for that user
    4. A starter Project (seeded with ProjectPolicy + owner membership)
    5. A sample ACTIVE TKO with one version
    """

    help = "Seed initial roles, admin user, role assignment, starter project, and example TKO"

    def handle(self, *args, **options):
        User = get_user_model()

        # --------------------------------------------------
        # 1) Seed Roles
        # --------------------------------------------------
        admin_role, _ = Role.objects.get_or_create(name=Role.Name.ADMIN)
        Role.objects.get_or_create(name=Role.Name.MANAGER)
        Role.objects.get_or_create(name=Role.Name.USER)
        self.stdout.write(self.style.SUCCESS("Roles ensured (ADMIN / MANAGER / USER)"))

        # --------------------------------------------------
        # 2) Seed Admin User (development only)
        # --------------------------------------------------
        admin_user, created = User.objects.get_or_create(
            username="admin",
            defaults={
                "email": "admin@example.com",
                "is_staff": True,
                "is_superuser": True,
            },
        )

        if created:
            admin_user.set_password("admin")  # dev-only password
            admin_user.save()
            self.stdout.write(self.style.WARNING("Admin user created (username=admin, password=admin)"))
        else:
            self.stdout.write(self.style.SUCCESS("Admin user already exists"))

        # --------------------------------------------------
        # 3) Seed explicit authority assignment (ORG ADMIN)
        # --------------------------------------------------
        UserRole.objects.get_or_create(
            user=admin_user,
            role=admin_role,
            scope_type=UserRole.ScopeType.ORG,
            project=None,
        )
        self.stdout.write(self.style.SUCCESS("Assigned ORG ADMIN role to admin user"))

        # --------------------------------------------------
        # 4) Seed Starter Project (SAFE: service + invariants)
        # --------------------------------------------------
        project = Project.objects.filter(name="Reasoning Workbench").first()
        if project is None:
            project = create_project(
                name="Reasoning Workbench",
                owner=admin_user,
                description="Prototype project for governed reasoning workbench",
                kind=Project.Kind.STANDARD,
            )
            self.stdout.write(self.style.WARNING("Starter project created"))
        else:
            # Ensure invariants exist even for legacy rows
            ensure_project_seeded(project=project)
            self.stdout.write(self.style.SUCCESS("Starter project already exists (invariants ensured)"))

        # --------------------------------------------------
        # 5) Seed Example TKO (Durable Object Proof)
        # --------------------------------------------------
        tko, created = KnowledgeObject.objects.get_or_create(
            object_type=KnowledgeObject.ObjectType.TKO,
            local_id="TKO-SEED-001",
            defaults={
                "title": "Seed TKO - Project Bootstrap",
                "domain": "Engineering",
                "scope_text": "Initial seed object to verify object durability",
                "status": KnowledgeObject.Status.ACTIVE,
                "owner": admin_user,
                "project": project,
            },
        )

        if created:
            KnowledgeObjectVersion.objects.create(
                obj=tko,
                version="0.1.0",
                content_text="Seed TKO created during initial bootstrap.",
                change_note="Initial seed",
                created_by=admin_user,
            )
            self.stdout.write(self.style.SUCCESS("Seed TKO created"))
        else:
            self.stdout.write(self.style.SUCCESS("Seed TKO already exists"))

        self.stdout.write(self.style.SUCCESS("Seeding complete"))
