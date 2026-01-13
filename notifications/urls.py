# -*- coding: utf-8 -*-
# notifications/urls.py
# Purpose:
# Notifications UI routes (prototype-safe GET actions)

from __future__ import annotations

from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.notification_list, name="list"),
    path("mark-all-read/", views.notification_mark_all_read, name="mark_all_read"),
    path("<int:notification_id>/<str:state>/", views.notification_set_read, name="set_read"),
]
