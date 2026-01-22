# -*- coding: utf-8 -*-
# accounts/urls.py
from __future__ import annotations

from django.contrib.auth import views as auth_views
from django.urls import path

from . import views, views_system

app_name = "accounts"

urlpatterns = [
    # Auth
    path("", auth_views.LoginView.as_view(template_name="accounts/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="accounts:login"), name="logout"),
    path("admin-hub/", views.admin_hub, name="admin_hub"),

    # Invite flow
    path("set-password/<uidb64>/<token>/", views.set_password_from_invite, name="set_password"),

    # Dashboard
    path("dashboard/", views.dashboard, name="dashboard"),
    path("projects/create/", views.project_create, name="project_create"),

    # Project chats
    path("projects/<int:project_id>/chats/", views.project_chat_list, name="project_chat_list"),
    path("projects/<int:project_id>/delete/", views.project_delete, name="project_delete"),


    # Chat routes
    path("config/chat/", views.chat_config_overrides, name="chat_config_overrides"),
    path("chats/", views.chat_list, name="chat_list"),
    path("chats/message/", views.chat_message_create, name="chat_message_create"),
    path("chats/browse/", views.chat_browse, name="chat_browse"),
    path("chats/<int:chat_id>/", views.chat_detail, name="chat_detail"),
    path("chats/new/", views.chat_create, name="chat_create"),
    path("chats/<int:chat_id>/rename/", views.chat_rename, name="chat_rename"),
    path("chats/<int:chat_id>/select/", views.chat_select, name="chat_select"),
    path("chats/<int:chat_id>/delete/", views.chat_delete, name="chat_delete"),


    # System (L1-L4 defaults) - superuser only
    path("config/system/", views_system.system_settings_home, name="system_settings_home"),
    path("config/system/level/<int:level>/", views_system.system_level_pick, name="system_settings_level_pick"),
    path("config/system/config/<int:config_id>/", views_system.system_config_detail, name="system_config_detail"),
    path(
        "config/system/config/<int:config_id>/versions/new/",
        views_system.system_config_version_new,
        name="system_config_version_new",
    ),





    # Active project (session)
    path("active-project/", views.active_project_set, name="active_project_set"),

    # Settings menu
    path("config/", views.config_menu, name="config_menu"),

    # User (L1)
    path("config/user/", views.user_config_edit, name="user_config_user"),
    path("config/user/definitions/", views.user_config_definitions, name="user_config_definitions"),
    path("config/user/info/", views.user_config_info, name="user_config_info"),

    # Project (L4)
    path("config/projects/", views.project_config_list, name="project_config_list"),
    path("config/projects/<int:project_id>/", views.project_config_edit, name="project_config_edit"),
    path("projects/<int:project_id>/select/", views.project_select, name="project_select"),
    path(
        "config/projects/<int:project_id>/definitions/",
        views.project_config_definitions,
        name="project_config_definitions",
    ),
    path("config/projects/<int:project_id>/info/", views.project_config_info, name="project_config_info"),

    # # Imports
    # path("imports/preview/", views.import_preview, name="import_preview"),
    # path("imports/preview/<str:conv_id>/", views.import_preview_detail, name="import_preview_detail"),

    # Session overrides
    path("session-overrides/", views.session_overrides_update, name="session_overrides_update"),
]
