# -*- coding: utf-8 -*-
# notifications/context_processors.py
# Purpose:
# Provide unread count to topbar without adding view logic everywhere.

from __future__ import annotations

from typing import Any, Dict

from django.contrib.auth.models import AnonymousUser

from notifications.models import Notification


def notifications_bar(request) -> Dict[str, Any]:
    user = getattr(request, "user", None)
    if not user or isinstance(user, AnonymousUser) or not user.is_authenticated:
        return {"rw_notifications": None}

    unread = Notification.objects.filter(recipient=user, is_read=False).count()

    return {
        "rw_notifications": {
            "unread_count": unread,
        }
    }
