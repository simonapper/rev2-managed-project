# -*- coding: utf-8 -*-
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0028_projecttopicchat"),
    ]

    operations = [
        migrations.AddField(
            model_name="projectplanningstage",
            name="key_variables",
            field=models.TextField(blank=True, default=""),
        ),
    ]
