# -*- coding: utf-8 -*-
# projects/urls.py
# Purpose:
# Project-scoped UI routes

from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from accounts import views as accounts_views
from projects import views
from projects import views_pde_ui
from projects.views_pde import pde_create_project_cko, pde_field_lock, pde_field_verify, pde_home, pde_commit
from projects import views_cko

app_name = "projects"

urlpatterns = [
    path("<int:project_id>/rename/", views.rename_project, name="rename_project"),
    path("<int:project_id>/preferences/", views.project_preferences, name="project_preferences"),
    path("pde/field/verify/", pde_field_verify, name="pde_field_verify"),
    path("pde/field/lock/", pde_field_lock, name="pde_field_lock"),
    path("pde/create_project_cko/", pde_create_project_cko, name="pde_create_project_cko"),
    path("<int:project_id>/pde/", pde_home, name="pde_home"),
    # path("<int:project_id>/mark_sandbox/", views.project_mark_sandbox, name="project_mark_sandbox"),
    path("<int:project_id>/pde/commit/", pde_commit, name="pde_commit"),
    path("<int:project_id>/pde/ui/", views_pde_ui.pde_detail, name="pde_detail"),
    path("<int:project_id>/home/", accounts_views.project_home, name="project_home"),
    path("<int:project_id>/cko/preview/", views_cko.cko_preview, name="cko_preview"),
    path("<int:project_id>/cko/<int:cko_id>/accept/", views_cko.cko_accept, name="cko_accept"),



]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
