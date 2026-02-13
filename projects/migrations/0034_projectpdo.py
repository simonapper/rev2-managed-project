from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0033_projectplanningstage_add_stage_process_assumptions"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProjectPDO",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.IntegerField(default=1)),
                ("status", models.CharField(choices=[("DRAFT", "Draft"), ("ACTIVE", "Active"), ("SUPERSEDED", "Superseded")], default="DRAFT", max_length=20)),
                ("content_json", models.JSONField(blank=True, default=dict)),
                ("seed_snapshot", models.JSONField(blank=True, default=dict)),
                ("change_summary", models.CharField(blank=True, default="", max_length=300)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(on_delete=models.PROTECT, related_name="project_pdos_created", to=settings.AUTH_USER_MODEL)),
                ("project", models.ForeignKey(on_delete=models.CASCADE, related_name="pdo_versions", to="projects.project")),
            ],
            options={
                "indexes": [
                    models.Index(fields=["project", "version"], name="projects_pr_project_1a2b3c_idx"),
                    models.Index(fields=["project", "status"], name="projects_pr_project_4d5e6f_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("project", "version"), name="uniq_project_pdo_version"),
                ],
            },
        ),
    ]
