# -*- coding: utf-8 -*-
# projects/signals.py

from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from config.models import ConfigRecord, ConfigScope, ConfigVersion
from .models import Project
from projects.services.project_bootstrap import bootstrap_project
from projects.services_project_membership import ensure_project_seeded




DEFAULT_L4_OPERATING_PROFILE_TEXT = """# ============================================================
# LEVEL 4 CONTEXT — OPERATING PROFILE (PROJECT)
# FILE NAME: operating_profile.session.conf
# PURPOSE: Configure a bounded, execution-oriented work session
#          for this project (defaults).
#
# SCOPE:
# Level 4 — Application / Session Configuration
# Project-scoped.
# ============================================================

Context

Domain: <Domain>
Subdomain: <Subdomain>
Scope: Single project
Goal: <What success means>

Work Type
- Selected Work Type: <e.g. Strategy / Engineering / Research>

Mode (Behavioural Preset)
- Active Mode: Analyst

Evaluation Posture
- Exploration tolerance: LOW
- Decision convergence required before stage advance: ON

Checkpointing Behaviour
- Prompt at stage transitions: ON
- Default checkpoint object: TKO

Off-Ramps
- If scope expands: checkpoint (TKO) or fork
# ============================================================
# END OF LEVEL 4 — OPERATING PROFILE
# ============================================================
"""


@receiver(post_save, sender=Project)
def project_post_create(sender, instance: Project, created: bool, **kwargs) -> None:
    if not created:
        return

    def _seed_everything() -> None:
        # 1) Container invariants (policy + OWNER membership)
        ensure_project_seeded(instance)

        # 2) Default project-scoped L4 config and set as active
        scope, _ = ConfigScope.objects.get_or_create(
            scope_type=ConfigScope.ScopeType.PROJECT,
            project=instance,
            defaults={"session_id": "", "user": None},
        )

        record, _ = ConfigRecord.objects.get_or_create(
            level=ConfigRecord.Level.L4,
            file_id="L4-OPERATING-PROFILE",
            scope=scope,
            defaults={
                "file_name": "operating_profile.session.conf",
                "status": ConfigRecord.Status.ACTIVE,
                "created_by": instance.owner,
            },
        )

        if not record.versions.exists():
            ConfigVersion.objects.create(
                config=record,
                version="0.1.0",
                content_text=DEFAULT_L4_OPERATING_PROFILE_TEXT,
                change_note="Auto-created default Project Level 4 Operating Profile.",
                created_by=instance.owner,
            )

        # Avoid calling instance.save() inside post_save
        if instance.active_l4_config_id != record.id:
            Project.objects.filter(pk=instance.pk).update(active_l4_config=record)

    # Run after commit so admin/shell creations are cleanly finalised
    transaction.on_commit(_seed_everything)
