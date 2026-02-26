from django.conf import settings
from django.db import migrations, models
from django.db.models import Q
import django.db.models.deletion


def _backfill_scope_user(apps, schema_editor):
    ContractText = apps.get_model("chats", "ContractText")
    UserModel = apps.get_model(settings.AUTH_USER_MODEL.split(".")[0], settings.AUTH_USER_MODEL.split(".")[1])

    valid_user_ids = set(UserModel.objects.values_list("id", flat=True))
    rows = ContractText.objects.all().iterator()
    for row in rows:
        updates = []
        if str(getattr(row, "scope_type", "") or "") == "USER":
            scope_id = getattr(row, "scope_id", None)
            scope_user_id = getattr(row, "scope_user_id", None)
            if scope_user_id is None and scope_id in valid_user_ids:
                row.scope_user_id = int(scope_id)
                updates.append("scope_user")
            if scope_id is None and scope_user_id is not None:
                row.scope_id = int(scope_user_id)
                updates.append("scope_id")
        if str(getattr(row, "scope_type", "") or "") == "GLOBAL_DEFAULT":
            if getattr(row, "scope_id", None) is not None:
                row.scope_id = None
                updates.append("scope_id")
            if getattr(row, "scope_user_id", None) is not None:
                row.scope_user_id = None
                updates.append("scope_user")
            if getattr(row, "scope_project_id", None) is not None:
                row.scope_project_id = None
                updates.append("scope_project")
        if updates:
            row.save(update_fields=updates + ["updated_at"])


def _noop_reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0062_projectdocument_archive_fields"),
        ("chats", "0019_chatworkspace_derax_enabled"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="contracttext",
            name="scope_project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="contract_text_rows",
                to="projects.project",
            ),
        ),
        migrations.AddField(
            model_name="contracttext",
            name="scope_user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="contract_text_scope_rows",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="contracttext",
            name="scope_type",
            field=models.CharField(
                choices=[
                    ("GLOBAL_DEFAULT", "Global default"),
                    ("USER", "User"),
                    ("PROJECT", "Project"),
                    ("PROJECT_USER", "Project + user"),
                ],
                max_length=20,
            ),
        ),
        migrations.RunPython(_backfill_scope_user, _noop_reverse),
        migrations.RemoveConstraint(
            model_name="contracttext",
            name="uq_contracttext_active_scoped",
        ),
        migrations.RemoveConstraint(
            model_name="contracttext",
            name="uq_contracttext_active_global",
        ),
        migrations.RemoveConstraint(
            model_name="contracttext",
            name="ck_contracttext_scope_shape",
        ),
        migrations.AddConstraint(
            model_name="contracttext",
            constraint=models.CheckConstraint(
                condition=(
                    (Q(scope_type="GLOBAL_DEFAULT") & Q(scope_id__isnull=True) & Q(scope_project__isnull=True) & Q(scope_user__isnull=True))
                    | (Q(scope_type="USER") & Q(scope_project__isnull=True) & (Q(scope_user__isnull=False) | Q(scope_id__isnull=False)))
                    | (Q(scope_type="PROJECT") & Q(scope_project__isnull=False) & Q(scope_user__isnull=True))
                    | (Q(scope_type="PROJECT_USER") & Q(scope_project__isnull=False) & Q(scope_user__isnull=False))
                ),
                name="ck_contracttext_scope_shape",
            ),
        ),
        migrations.AddConstraint(
            model_name="contracttext",
            constraint=models.UniqueConstraint(
                condition=Q(scope_type="GLOBAL_DEFAULT", status="ACTIVE"),
                fields=("key", "scope_type"),
                name="uq_contracttext_active_global_default",
            ),
        ),
        migrations.AddConstraint(
            model_name="contracttext",
            constraint=models.UniqueConstraint(
                condition=Q(scope_type="USER", scope_user__isnull=False, status="ACTIVE"),
                fields=("key", "scope_type", "scope_user"),
                name="uq_contracttext_active_user",
            ),
        ),
        migrations.AddConstraint(
            model_name="contracttext",
            constraint=models.UniqueConstraint(
                condition=Q(scope_type="PROJECT", scope_project__isnull=False, status="ACTIVE"),
                fields=("key", "scope_type", "scope_project"),
                name="uq_contracttext_active_project",
            ),
        ),
        migrations.AddConstraint(
            model_name="contracttext",
            constraint=models.UniqueConstraint(
                condition=Q(scope_type="PROJECT_USER", scope_project__isnull=False, scope_user__isnull=False, status="ACTIVE"),
                fields=("key", "scope_type", "scope_project", "scope_user"),
                name="uq_contracttext_active_project_user",
            ),
        ),
        migrations.AddIndex(
            model_name="contracttext",
            index=models.Index(fields=["key", "scope_type", "scope_project", "scope_user"], name="chats_contr_key_a7b81b_idx"),
        ),
        migrations.AddIndex(
            model_name="contracttext",
            index=models.Index(fields=["scope_type", "scope_project", "scope_user", "status"], name="chats_contr_scope_t_45a048_idx"),
        ),
    ]
