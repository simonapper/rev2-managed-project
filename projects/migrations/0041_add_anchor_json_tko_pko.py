from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0040_rename_projects_anchor_project_marker_idx_projects_pr_project_03aedb_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="projectanchor",
            name="content_json",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="projectcko",
            name="content_json",
            field=models.JSONField(blank=True, default=dict, help_text="Structured JSON view of the CKO."),
        ),
        migrations.CreateModel(
            name="ProjectTKO",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.IntegerField(default=1)),
                ("status", models.CharField(choices=[("DRAFT", "Draft"), ("ACCEPTED", "Accepted"), ("SUPERSEDED", "Superseded")], default="DRAFT", max_length=20)),
                ("content_text", models.TextField(blank=True, default="")),
                ("content_json", models.JSONField(blank=True, default=dict)),
                ("content_html", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tko_versions", to="projects.project")),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="project_tkos_created", to=settings.AUTH_USER_MODEL)),
                ("accepted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="project_tkos_accepted", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "indexes": [
                    models.Index(fields=["project", "version"], name="projects_pr_project_3c1b54_idx"),
                    models.Index(fields=["project", "status"], name="projects_pr_project_5dcb8f_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="ProjectPKO",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.IntegerField(default=1)),
                ("status", models.CharField(choices=[("DRAFT", "Draft"), ("ACCEPTED", "Accepted"), ("SUPERSEDED", "Superseded")], default="DRAFT", max_length=20)),
                ("content_text", models.TextField(blank=True, default="")),
                ("content_json", models.JSONField(blank=True, default=dict)),
                ("content_html", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pko_versions", to="projects.project")),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="project_pkos_created", to=settings.AUTH_USER_MODEL)),
                ("accepted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="project_pkos_accepted", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "indexes": [
                    models.Index(fields=["project", "version"], name="projects_pr_project_83e8f3_idx"),
                    models.Index(fields=["project", "status"], name="projects_pr_project_2f6b7b_idx"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="projecttko",
            constraint=models.UniqueConstraint(fields=("project", "version"), name="uniq_project_tko_version"),
        ),
        migrations.AddConstraint(
            model_name="projectpko",
            constraint=models.UniqueConstraint(fields=("project", "version"), name="uniq_project_pko_version"),
        ),
    ]
