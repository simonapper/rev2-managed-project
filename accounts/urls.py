# -*- coding: utf-8 -*-
# accounts/urls.py
# WHOLE FILE

from __future__ import annotations

from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    # Auth
    path("", auth_views.LoginView.as_view(template_name="accounts/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="accounts:login"), name="logout"),

    # Invite flow
    path("set-password/<uidb64>/<token>/", views.set_password_from_invite, name="set_password"),

# App
    path("dashboard/", views.dashboard, name="dashboard"),
    path("projects/create/", views.project_create, name="project_create"),
    path("projects/<int:project_id>/chats/", views.project_chat_list, name="project_chat_list"),

    path("chats/", views.chat_list, name="chat_list"),
    path("chats/message/", views.chat_message_create, name="chat_message_create"),
    path("chats/browse/", views.chat_browse, name="chat_browse"),

        # Active project (session)
    path("active-project/", views.active_project_set, name="active_project_set"),

    # Settings
    path("config/", views.config_menu, name="config_menu"),

    # User (L1)
    path("config/user/", views.user_config_edit, name="user_config_user"),
    path("config/user/definitions/", views.user_config_definitions, name="user_config_definitions"),
    path("config/user/info/", views.user_config_info, name="user_config_info"),

    # Project (L4)
    path("config/projects/", views.project_config_list, name="project_config_list"),
    path("config/projects/<int:project_id>/", views.project_config_edit, name="project_config_edit"),
    path(
        "config/projects/<int:project_id>/definitions/",
        views.project_config_definitions,
        name="project_config_definitions",
    ),
    path("config/projects/<int:project_id>/info/", views.project_config_info, name="project_config_info"),

    # Session overrides
    path("session-overrides/", views.session_overrides_update, name="session_overrides_update"),
]
