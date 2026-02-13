from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0036_update_ppde_contracts_pdo"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="projectplanningstage",
            name="description",
        ),
        migrations.RemoveField(
            model_name="projectplanningstage",
            name="entry_condition",
        ),
        migrations.RemoveField(
            model_name="projectplanningstage",
            name="acceptance_statement",
        ),
        migrations.RemoveField(
            model_name="projectplanningstage",
            name="exit_condition",
        ),
        migrations.RemoveField(
            model_name="projectplanningstage",
            name="key_variables",
        ),
        migrations.RemoveField(
            model_name="projectplanningstage",
            name="key_deliverables",
        ),
    ]
