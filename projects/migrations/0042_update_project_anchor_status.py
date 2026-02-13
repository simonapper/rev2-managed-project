from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def map_anchor_statuses(apps, schema_editor):
    ProjectAnchor = apps.get_model("projects", "ProjectAnchor")
    ProjectAnchor.objects.filter(status="ACCEPTED").update(status="PASS_LOCKED")


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0041_add_anchor_json_tko_pko"),
    ]

    operations = [
        migrations.AlterField(
            model_name="projectanchor",
            name="status",
            field=models.CharField(
                max_length=16,
                choices=[
                    ("DRAFT", "Draft"),
                    ("PROPOSED", "Proposed"),
                    ("PASS_LOCKED", "Locked"),
                ],
                default="DRAFT",
            ),
        ),
        migrations.AddField(
            model_name="projectanchor",
            name="proposed_by",
            field=models.ForeignKey(
                to=settings.AUTH_USER_MODEL,
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                related_name="anchor_proposals",
            ),
        ),
        migrations.AddField(
            model_name="projectanchor",
            name="proposed_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="projectanchor",
            name="locked_by",
            field=models.ForeignKey(
                to=settings.AUTH_USER_MODEL,
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                related_name="anchor_locks",
            ),
        ),
        migrations.AddField(
            model_name="projectanchor",
            name="locked_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="projectanchor",
            name="last_edited_by",
            field=models.ForeignKey(
                to=settings.AUTH_USER_MODEL,
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                related_name="anchor_edits",
            ),
        ),
        migrations.AddField(
            model_name="projectanchor",
            name="last_edited_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.RunPython(map_anchor_statuses, migrations.RunPython.noop),
    ]
