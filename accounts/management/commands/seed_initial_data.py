# accounts/management/commands/seed_initial_data.py

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

# Import domain models we want to seed
from accounts.models import Role, UserRole
from projects.models import Project
from objects.models import KnowledgeObject, KnowledgeObjectVersion


class Command(BaseCommand):
    """
    Seed minimal, safe initial data for local development and prototypes.

    This command is intentionally:
    - idempotent (safe to run multiple times)
    - conservative (no destructive actions)
    - explicit (no hidden side effects)

    It seeds:
    1. Core roles (ADMIN / MANAGER / USER)
    2. A local admin user (dev only)
    3. ORG-scoped ADMIN UserRole assignment for that user
    4. A starter Project
    5. A sample ACTIVE TKO with one version
    """

    help = "Seed initial roles, admin user, role assignment, starter project, and example TKO"

    def handle(self, *args, **options):
        # Resolve the active User model (supports custom user models)
        User = get_user_model()

        # --------------------------------------------------
        # 1. Seed Roles
        # --------------------------------------------------
        # Roles are part of the system's authority model.
        # These are stable and should never be deleted casually.
        admin_role, _ = Role.objects.get_or_create(name=Role.Name.ADMIN)
        Role.objects.get_or_create(name=Role.Name.MANAGER)
        Role.objects.get_or_create(name=Role.Name.USER)

        self.stdout.write(self.style.SUCCESS("Roles ensured (ADMIN / MANAGER / USER)"))

        # --------------------------------------------------
        # 2. Seed Admin User (development only)
        # --------------------------------------------------
        # This creates a superuser for local testing.
        # Password is intentionally simple; do NOT use in production.
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
            self.stdout.write(
                self.style.WARNING("Admin user created (username=admin, password=admin)")
            )
        else:
            self.stdout.write(self.style.SUCCESS("Admin user already exists"))

        # --------------------------------------------------
        # 3. Seed explicit authority assignment (ORG ADMIN)
        # --------------------------------------------------
        # This assigns your application's governance role model explicitly.
        # It is separate from Django's is_superuser flag.
        UserRole.objects.get_or_create(
            user=admin_user,
            role=admin_role,
            scope_type=UserRole.ScopeType.ORG,
            project=None,
        )
        self.stdout.write(self.style.SUCCESS("Assigned ORG ADMIN role to admin user"))

        # --------------------------------------------------
        # 4. Seed Starter Project
        # --------------------------------------------------
        # This project acts as a sandbox for verifying:
        # - object durability
        # - admin UI behaviour
        project, _ = Project.objects.get_or_create(
            name="Reasoning Workbench",
            defaults={
                "description": "Prototype project for governed reasoning workbench",
                "owner": admin_user,
            },
        )

        self.stdout.write(self.style.SUCCESS("Starter project ensured"))

        # --------------------------------------------------
        # 5. Seed Example TKO (Durable Object Proof)
        # --------------------------------------------------
        # This verifies that:
        # - KnowledgeObjects persist correctly
        # - Versioning works
        # - TKOs can exist independently of chats
        tko, created = KnowledgeObject.objects.get_or_create(
            object_type="TKO",
            local_id="TKO-SEED-001",
            defaults={
                "title": "Seed TKO - Project Bootstrap",
                "domain": "Engineering",
                "scope_text": "Initial seed object to verify object durability",
                "status": "ACTIVE",
                "owner": admin_user,
                "project": project,
            },
        )

        # Only create a version if the object was newly created
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

        # --------------------------------------------------
        # Done
        # --------------------------------------------------
        self.stdout.write(self.style.SUCCESS("Seeding complete"))
