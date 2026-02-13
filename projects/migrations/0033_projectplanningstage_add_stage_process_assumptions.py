from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0032_ppde_stage_backfill_inputs_outputs"),
    ]

    operations = [
        migrations.AddField(
            model_name="projectplanningstage",
            name="stage_process",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="projectplanningstage",
            name="assumptions",
            field=models.TextField(blank=True, default=""),
        ),
    ]
