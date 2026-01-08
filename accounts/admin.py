# accounts/admin.py
# -*- coding: utf-8 -*-

from django import forms
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from accounts.models import Role, UserRole
from . import admin_avatars  # noqa: F401

User = get_user_model()


class UserCreateInviteForm(forms.ModelForm):
    """
    Admin "Add user" form for the invite flow (Option A).

    Collects identity fields only (username/email).
    Does NOT ask for a password; password is set via emailed link.
    """

    class Meta:
        model = User
        fields = ("username", "email", "is_active")


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    """
    User admin (identity).

    IMPORTANT DISTINCTION:
    - This manages identity (username/email/password/staff flags).
    - Application authority (ADMIN/MANAGER/USER) is managed via UserRole.

    INVITE FLOW (Option A):
    - On creating a user in admin:
        * set unusable password
        * email one-time "set password" link
    """

    # Use our no-password add form
    add_form = UserCreateInviteForm

    # List page
    list_display = ("username", "email", "is_staff", "is_superuser", "is_active", "last_login")
    search_fields = ("username", "email", "first_name", "last_name")
    ordering = ("username",)

    # Edit form layout (change view)
    fieldsets = (
        (None, {"fields": ("username", "email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )

    # Add form layout (add view) — no password fields by design
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("username", "email", "is_active"),
        }),
    )

    def save_model(self, request, obj, form, change):
        """
        When creating a new user via admin:
        - set unusable password
        - send one-time set-password invite email

        When editing an existing user:
        - no invite behaviour
        """
        is_new = obj.pk is None

        # Save first to allocate pk
        super().save_model(request, obj, form, change)

        if not is_new:
            return

        # Require email for invite workflow
        if not obj.email:
            messages.warning(
                request,
                "User created, but no email address was provided. No invite email was sent.",
            )
            return

        # Ensure the user cannot authenticate until they set a password via the invite link
        obj.set_unusable_password()
        obj.save(update_fields=["password"])

        # Build invite link (one-time token)
        uidb64 = urlsafe_base64_encode(force_bytes(obj.pk))
        token = default_token_generator.make_token(obj)
        path = reverse("accounts:set_password", kwargs={"uidb64": uidb64, "token": token})
        invite_url = request.build_absolute_uri(path)

        # Send invite email (console backend in dev)
        send_mail(
            subject="Set your password",
            message=(
                "Welcome.\n\n"
                "Please set your password using the link below:\n\n"
                f"{invite_url}\n\n"
                "If you did not expect this email, you can ignore it."
            ),
            from_email=None,  # uses DEFAULT_FROM_EMAIL
            recipient_list=[obj.email],
        )

        messages.success(request, f"Invite email sent to {obj.email}.")


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    """
    Role taxonomy: ADMIN / MANAGER / USER
    """
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    """
    Application authority assignments (scoped roles).

    ORG scope     -> project must be NULL
    PROJECT scope -> project must be set
    """
    list_display = ("user", "role", "scope_type", "project", "created_at")
    list_filter = ("scope_type", "role")
    search_fields = ("user__username", "user__email", "project__name")
    autocomplete_fields = ("user", "role", "project")
