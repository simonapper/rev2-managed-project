from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0034_projectpdo"),
    ]

    operations = [
        migrations.AddField(
            model_name="projectplanningpurpose",
            name="pdo_summary",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="projectplanningpurpose",
            name="planning_constraints",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="projectplanningpurpose",
            name="assumptions",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="projectplanningpurpose",
            name="cko_alignment_stage1_inputs_match",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="projectplanningpurpose",
            name="cko_alignment_final_outputs_match",
            field=models.TextField(blank=True, default=""),
        ),
    ]
