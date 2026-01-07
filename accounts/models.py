# accounts/models.py
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Custom user model for the Workbench.

    Pattern A: keep Django username field for compatibility,
    but require unique email and (optionally) allow login via email.
    """
    email = models.EmailField(unique=True)


class Role(models.Model):
    """
    High-level role taxonomy.
    This is deliberately small and stable.
    """

    class Name(models.TextChoices):
        ADMIN = "ADMIN", "Admin"         # Global authority
        MANAGER = "MANAGER", "Manager"   # Project-scoped authority
        USER = "USER", "User"            # Contributor

    name = models.CharField(max_length=20, choices=Name.choices, unique=True)

    def __str__(self) -> str:
        return self.get_name_display()


class UserRole(models.Model):
    """
    Assigns a Role to a User with an explicit scope.

    Scope rules:
    - ORG     -> applies globally (project must be NULL)
    - PROJECT -> applies only to a specific project
    """

    class ScopeType(models.TextChoices):
        ORG = "ORG", "Organisation"
        PROJECT = "PROJECT", "Project"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    scope_type = models.CharField(max_length=20, choices=ScopeType.choices)

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="scoped_roles",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "role", "scope_type", "project")]
