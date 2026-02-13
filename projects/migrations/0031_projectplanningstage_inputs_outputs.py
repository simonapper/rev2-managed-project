from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0030_seed_plan_from_stages_contract"),
    ]

    operations = [
        migrations.AddField(
            model_name="projectplanningstage",
            name="inputs",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="projectplanningstage",
            name="outputs",
            field=models.TextField(blank=True, default=""),
        ),
    ]
