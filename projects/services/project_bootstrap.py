# -*- coding: utf-8 -*-
# projects/services/project_bootstrap.py

from __future__ import annotations

import os
from django.conf import settings
from django.db import transaction

from projects.models import Project, ProjectPolicy, UserProjectPrefs


def bootstrap_project(*, project: Project) -> Project:
    with transaction.atomic():

        # 1) Ensure artefact root (once)
        if not (project.artefact_root_ref or "").strip():
            artefact_root = f"projects/{project.id}/artefacts/"
            full_path = os.path.join(settings.MEDIA_ROOT, artefact_root)
            os.makedirs(full_path, exist_ok=True)
            project.artefact_root_ref = artefact_root
            project.save(update_fields=["artefact_root_ref", "updated_at"])

        # 2) Ensure policy exists
        ProjectPolicy.objects.get_or_create(project=project)

        # 3) Ensure owner prefs exist
        UserProjectPrefs.objects.get_or_create(project=project, user=project.owner)

        return project
