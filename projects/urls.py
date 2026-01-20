# -*- coding: utf-8 -*-
# projects/urls.py
# Purpose:
# Project-scoped UI routes

from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from projects import views

app_name = "projects"

urlpatterns = [
    path("<int:project_id>/rename/", views.rename_project, name="rename_project"),
    path("<int:project_id>/preferences/", views.project_preferences, name="project_preferences"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
