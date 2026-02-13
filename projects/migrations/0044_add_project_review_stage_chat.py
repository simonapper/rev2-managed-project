from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0043_merge_20260211_1827"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProjectReviewStageChat",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("marker", models.CharField(max_length=16)),
                ("stage_number", models.IntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("chat", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="review_stage_binding", to="chats.chatworkspace")),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="review_stage_chats", to="projects.project")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="review_stage_chats", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("project", "user", "marker", "stage_number"), name="uq_project_review_stage_chat")
                ],
                "indexes": [
                    models.Index(fields=["project", "user", "marker"], name="ix_review_stage_p_u_m"),
                    models.Index(fields=["project", "marker", "stage_number"], name="ix_review_stage_p_m_s"),
                ],
            },
        ),
    ]
