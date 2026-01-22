# -*- coding: utf-8 -*-
# config_ui/urls.py

from __future__ import annotations

from django.urls import path

from . import views_system

app_name = "config_ui"

urlpatterns = [
    # System config UI (ORG-scoped defaults for Levels 1-4)
    path("system/config/", views_system.system_home, name="system_home"),
    path("system/config/level/<int:level>/", views_system.system_level_browse, name="system_level_browse"),
    path("system/config/new/<int:level>/", views_system.system_config_create, name="system_config_create"),
    path("system/config/<int:config_id>/", views_system.system_config_detail, name="system_config_detail"),
    path(
        "system/config/<int:config_id>/versions/new/",
        views_system.system_config_version_new,
        name="system_config_version_new",
    ),
    path(
        "system/config/<int:config_id>/set-active/",
        views_system.system_set_active,
        name="system_set_active",
    ),
]
