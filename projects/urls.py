# -*- coding: utf-8 -*-
# projects/urls.py
# Purpose:
# Notifications UI routes (prototype-safe GET actions)

from django.conf import settings
from django.conf.urls.static import static
from django.urls import path, include
from projects import views

app_name = "projects"

urlpatterns = [
    path("<int:project_id>/rename/", views.rename_project, name="rename_project"),
    # add other project-specific routes here
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
