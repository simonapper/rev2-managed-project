# -*- coding: utf-8 -*-
# accounts/urls.py

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

    # Configuration
    path("config/", views.config_menu, name="config_menu"),
    path("config/user/", views.user_config_edit, name="user_config_user"),
    path("config/user/definitions/", views.user_config_definitions, name="user_config_definitions"),
    path("config/user/info/", views.user_config_info, name="user_config_info"),
    path("session-overrides/", views.session_overrides_update, name="session_overrides_update"),

]