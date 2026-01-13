# -*- coding: utf-8 -*-
# notifications/views.py
# Purpose:
# Minimal notifications UI for prototype:
# - list unread/all
# - mark read/unread (GET to avoid nested-form issues)
# - mark all read (GET to avoid nested-form issues)

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from notifications.models import Notification


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

    return redirect(request.GET.get("next") or "notifications:list")


@login_required
def notification_mark_all_read(request):
    """
    Prototype: GET endpoint to avoid nested-form submission failures.
    """
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    return redirect(request.GET.get("next") or "notifications:list")
