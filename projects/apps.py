# -*- coding: utf-8 -*-
# projects/apps.py

from __future__ import annotations

from django.apps import AppConfig


class ProjectsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "projects"

    def ready(self) -> None:
        # Ensure signal handlers are registered.
        from . import signals  # noqa: F401
