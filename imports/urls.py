# -*- coding: utf-8 -*-
# imports/urls.py

from django.urls import path
from . import views

app_name = "imports"

urlpatterns = [
    path("preview/", views.preview_import, name="preview_import"),
    path("preview/<int:idx>/", views.import_preview_detail, name="import_preview_detail"),
    path("import/", views.import_chatgpt_action, name="import_chatgpt_action"),
]
