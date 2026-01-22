# -*- coding: utf-8 -*-
# notifications/views.py

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from notifications.models import Notification


def _safe_next(request, fallback_url_name: str) -> str:
    """
    Allow only safe same-host redirects.
    Also block known POST-only endpoints that break if hit via GET.
    """
    nxt = (request.GET.get("next") or "").strip()
    if not nxt:
        return reverse(fallback_url_name)

    if not url_has_allowed_host_and_scheme(
        url=nxt,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return reverse(fallback_url_name)

    # Block POST-only endpoints explicitly (prevents redirect loops / 405 JSON)
    if nxt.startswith("/accounts/chats/message/"):
        return reverse(fallback_url_name)

    return nxt


@login_required
def notification_list(request):
    show = (request.GET.get("show", "unread") or "").lower()
    qs = Notification.objects.filter(recipient=request.user).order_by("-created_at")

    if show != "all":
        show = "unread"
        qs = qs.filter(is_read=False)

    unread_count = Notification.objects.filter(recipient=request.user, is_read=False).count()

    return render(
        request,
        "notifications/notification_list.html",
        {
            "notifications": qs[:200],
            "show": show,
            "unread_count": unread_count,
        },
    )


@login_required
def notification_set_read(request, notification_id: int, state: str):
    """
    Prototype: GET endpoint to avoid nested-form submission failures.
    state: "read" | "unread"
    """
    state = (state or "").lower().strip()
    if state not in {"read", "unread"}:
        raise Http404()

    n = get_object_or_404(Notification, pk=notification_id, recipient=request.user)
    n.is_read = (state == "read")
    n.save(update_fields=["is_read"])

    return redirect(_safe_next(request, "notifications:list"))


@login_required
def notification_mark_all_read(request):
    """
    Prototype: GET endpoint to avoid nested-form submission failures.
    """
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    return redirect(_safe_next(request, "notifications:list"))
