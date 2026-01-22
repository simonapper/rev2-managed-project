# projects/services/project_bootstrap.py
# -*- coding: utf-8 -*-

import os
from django.conf import settings
from django.db import transaction

from projects.models import Project, ProjectPolicy, UserProjectPrefs


def bootstrap_project(
    *,
    owner,                 # caller unless UI assigns (manager/superuser)
    kind,
    name=None,             # optional override
    description="",
    l1_config=None,
    l2_config=None,
    l3_config=None,
):
    with transaction.atomic():

        # 1) Create or get project
        default_name = f"{owner.username}.Sandbox" if kind == Project.Kind.SANDBOX else None

        project, created = Project.objects.get_or_create(
            owner=owner,
            kind=kind,
            defaults={
                "name": name or default_name,
                "description": description,
            },
        )

        # Allow UI to rename after creation (no lock)
        if name and project.name != name:
            project.name = name
            project.save(update_fields=["name"])

        # 2) Create artefact root (once)
        if not getattr(project, "artefact_root_ref", None):
            artefact_root = f"projects/{project.id}/artefacts/"
            full_path = os.path.join(settings.MEDIA_ROOT, artefact_root)
            os.makedirs(full_path, exist_ok=True)

            project.artefact_root_ref = artefact_root
            project.save(update_fields=["artefact_root_ref"])

        # 3) Ensure policy exists
        policy, _ = ProjectPolicy.objects.get_or_create(project=project)

        # 4) Attach L1-L3 configs (sandbox = defaults, standard = explicit)
        if l1_config:
            policy.active_l1_config = l1_config
        if l2_config:
            policy.active_l2_config = l2_config
        if l3_config:
            policy.active_l3_config = l3_config

        policy.save()

        # 5) Ensure owner prefs exist
        UserProjectPrefs.objects.get_or_create(
            project=project,
            user=owner,
        )

        return project
