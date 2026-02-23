# -*- coding: utf-8 -*-

from django.db import migrations


def _forward_seed_to_refine(apps, schema_editor):
    WorkItem = apps.get_model("projects", "WorkItem")
    WorkItem.objects.filter(active_phase="SEED").update(active_phase="REFINE")


def _reverse_refine_to_seed(apps, schema_editor):
    WorkItem = apps.get_model("projects", "WorkItem")
    WorkItem.objects.filter(active_phase="REFINE").update(active_phase="SEED")


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0055_workitem_title_intent_raw"),
    ]

    operations = [
        migrations.RunPython(_forward_seed_to_refine, _reverse_refine_to_seed),
    ]
