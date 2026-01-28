# -*- coding: utf-8 -*-
# projects/urls.py
# Purpose:
# Project-scoped UI routes

from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from projects import views
from projects.views_pde import pde_create_project_cko, pde_field_lock, pde_field_verify, pde_home

app_name = "projects"

urlpatterns = [
    path("<int:project_id>/rename/", views.rename_project, name="rename_project"),
    path("<int:project_id>/preferences/", views.project_preferences, name="project_preferences"),
    path("pde/field/verify/", pde_field_verify, name="pde_field_verify"),
    path("pde/field/lock/", pde_field_lock, name="pde_field_lock"),
    path("pde/create_project_cko/", pde_create_project_cko, name="pde_create_project_cko"),
    path("<int:project_id>/pde/", pde_home, name="pde_home"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
