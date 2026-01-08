# -*- coding: utf-8 -*-
# accounts/views.py

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import urlsafe_base64_decode

from accounts.forms import UserProfileDefaultsForm
from django.views.decorators.http import require_POST

User = get_user_model()


@require_POST
@login_required
def session_overrides_update(request):
    """
    Stores session-scoped avatar overrides (Level 4 behaviour hint).
    Confirm happens client-side (JS). Server trusts POST as confirmed.
    """
    category = (request.POST.get("category") or "").strip().upper()
    avatar_id = (request.POST.get("avatar_id") or "").strip()

    allowed = {"COGNITIVE","INTERACTION","PRESENTATION","EPISTEMIC","PERFORMANCE","CHECKPOINTING"}
    if category not in allowed:
        messages.error(request, "Invalid override category.")
        return redirect(request.META.get("HTTP_REFERER", "accounts:dashboard"))

    key = f"rw_l4_override_{category}"

    # Empty means clear override
    if not avatar_id:
        request.session.pop(key, None)
        messages.success(request, f"{category.title()} override cleared for this session.")
        return redirect(request.META.get("HTTP_REFERER", "accounts:dashboard"))

    # Validate avatar exists + active + category matches
    try:
        av = Avatar.objects.get(pk=int(avatar_id), is_active=True, category=getattr(Avatar.Category, category))
    except Exception:
        messages.error(request, "Invalid avatar selection.")
        return redirect(request.META.get("HTTP_REFERER", "accounts:dashboard"))

    request.session[key] = str(av.pk)
    messages.success(request, f"{category.title()} override set to {av.name} (session only).")
    return redirect(request.META.get("HTTP_REFERER", "accounts:dashboard"))



@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    return render(request, "accounts/dashboard.html")


@login_required
def config_menu(request: HttpRequest) -> HttpResponse:
    return render(request, "accounts/config_menu.html")


@login_required
def user_config_edit(request: HttpRequest) -> HttpResponse:
    profile = request.user.profile

    if request.method == "POST":
        form = UserProfileDefaultsForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, "Defaults updated.")
            return redirect("accounts:user_config_user")
    else:
        form = UserProfileDefaultsForm(instance=profile)

    return render(request, "accounts/config_user_edit.html", {"form": form})


@login_required
def user_config_definitions(request: HttpRequest) -> HttpResponse:
    return render(request, "accounts/config_user_definitions.html")


@login_required
def user_config_info(request: HttpRequest) -> HttpResponse:
    return render(request, "accounts/config_user_info.html")


def set_password_from_invite(request: HttpRequest, uidb64: str, token: str) -> HttpResponse:
    try:
        uid = int(urlsafe_base64_decode(uidb64).decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError):
        uid = None

    user = get_object_or_404(User, pk=uid)

    if not default_token_generator.check_token(user, token):
        return render(request, "accounts/invite_invalid.html", status=400)

    if request.method == "POST":
        password1 = (request.POST.get("password1") or "").strip()
        password2 = (request.POST.get("password2") or "").strip()

        if not password1 or not password2:
            messages.error(request, "Please enter your password twice.")
        elif password1 != password2:
            messages.error(request, "Passwords do not match.")
        else:
            user.set_password(password1)
            user.save(update_fields=["password"])
            messages.success(request, "Password set. You can now log in.")
            return redirect("accounts:login")

    return render(request, "accounts/set_password.html", {"uidb64": uidb64, "token": token})
