from django.db import migrations


def _to_lines(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [s.strip() for s in value.splitlines() if s.strip()]
    return [str(value).strip()]


def forwards(apps, schema_editor):
    Stage = apps.get_model("projects", "ProjectPlanningStage")
    for row in Stage.objects.all():
        changed = False

        if not (row.inputs or "").strip():
            src = (row.entry_condition or "").strip()
            if src:
                row.inputs = src
                changed = True

        if not (row.outputs or "").strip():
            lines = _to_lines(row.key_deliverables)
            if lines:
                row.outputs = "\n".join(lines)
                changed = True

        if not (row.risks_notes or "").strip():
            parts = []
            desc = (row.description or "").strip()
            acc = (row.acceptance_statement or "").strip()
            exit_c = (row.exit_condition or "").strip()
            if desc:
                parts.append("DESCRIPTION: " + desc)
            if acc:
                parts.append("ACCEPTANCE: " + acc)
            if exit_c:
                parts.append("EXIT: " + exit_c)
            if parts:
                row.risks_notes = "\n".join(parts)
                changed = True

        if changed:
            row.save(
                update_fields=[
                    "inputs",
                    "outputs",
                    "risks_notes",
                    "updated_at",
                ]
            )


def backwards(apps, schema_editor):
    # Data copy is one-way; keep existing values on rollback.
    return


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0031_projectplanningstage_inputs_outputs"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
