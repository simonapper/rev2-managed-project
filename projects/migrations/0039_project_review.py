from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0038_merge_20260210_1826"),
        ("chats", "0012_chatworkspace_last_answer_at_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProjectAnchor",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("marker", models.CharField(choices=[("INTENT", "Intent"), ("ROUTE", "Route"), ("EXECUTE", "Execute"), ("COMPLETE", "Complete")], max_length=16)),
                ("content", models.TextField(blank=True, default="")),
                ("status", models.CharField(choices=[("DRAFT", "Draft"), ("ACCEPTED", "Accepted")], default="DRAFT", max_length=16)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="anchors", to="projects.project")),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=["project", "marker"], name="uq_project_anchor"),
                ],
                "indexes": [
                    models.Index(fields=["project", "marker"], name="projects_anchor_project_marker_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="ProjectReviewChat",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("marker", models.CharField(max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("chat", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="review_binding", to="chats.chatworkspace")),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="review_chats", to="projects.project")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="review_chats", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=["project", "user", "marker"], name="uq_project_review_chat"),
                ],
                "indexes": [
                    models.Index(fields=["project", "user", "marker"], name="projects_review_project_user_marker_idx"),
                ],
            },
        ),
    ]
