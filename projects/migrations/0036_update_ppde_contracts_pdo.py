from django.db import migrations


STRUCTURE_PURPOSE = (
    "Generate a PDO (Planning Direction Object) from the accepted CKO and PPDE context."
)
STRUCTURE_INPUTS = (
    "CKO snapshot, seed summary (if present), and existing PPDE fields."
)
STRUCTURE_OUTPUTS = (
    "Return JSON only with fields:\n"
    "- pdo_summary\n"
    "- cko_alignment: {stage1_inputs_match, final_outputs_match}\n"
    "- planning_purpose\n"
    "- planning_constraints\n"
    "- assumptions\n"
    "- stages[]: {stage_number, status, title, purpose, inputs, stage_process, outputs, assumptions, duration_estimate, risks_notes}\n"
    "Rules: outputs are one per line; stage_process is 1-3 sentences."
)
STRUCTURE_METHOD = (
    "Sources order: CKO -> planning_purpose -> current draft -> other artefacts.\n"
    "Do not invent project facts; ask if missing.\n"
    "When finalising: JSON only, no markdown or commentary."
)
STRUCTURE_ACCEPTANCE = (
    "All required fields present; stages are ordered; JSON only output."
)

TRANSFORM_PURPOSE = (
    "Transform or refine a single PPDE stage to match the PDO stage schema."
)
TRANSFORM_INPUTS = (
    "Current stage draft, planning purpose, and CKO snapshot."
)
TRANSFORM_OUTPUTS = (
    "Return JSON only with fields:\n"
    "{title, purpose, inputs, stage_process, outputs, assumptions, duration_estimate, risks_notes}"
)
TRANSFORM_METHOD = (
    "Sources order: CKO -> planning_purpose -> current stage -> user clarifications.\n"
    "Do not invent project facts; ask if missing.\n"
    "When finalising: JSON only, no markdown or commentary."
)
TRANSFORM_ACCEPTANCE = (
    "All required fields present; outputs one per line; JSON only output."
)


def forwards(apps, schema_editor):
    PhaseContract = apps.get_model("projects", "PhaseContract")
    for contract in PhaseContract.objects.filter(key="STRUCTURE_PROJECT", is_active=True):
        contract.purpose_text = STRUCTURE_PURPOSE
        contract.inputs_text = STRUCTURE_INPUTS
        contract.outputs_text = STRUCTURE_OUTPUTS
        contract.method_guidance_text = STRUCTURE_METHOD
        contract.acceptance_test_text = STRUCTURE_ACCEPTANCE
        contract.save(update_fields=[
            "purpose_text",
            "inputs_text",
            "outputs_text",
            "method_guidance_text",
            "acceptance_test_text",
        ])

    for contract in PhaseContract.objects.filter(key="TRANSFORM_STAGE", is_active=True):
        contract.purpose_text = TRANSFORM_PURPOSE
        contract.inputs_text = TRANSFORM_INPUTS
        contract.outputs_text = TRANSFORM_OUTPUTS
        contract.method_guidance_text = TRANSFORM_METHOD
        contract.acceptance_test_text = TRANSFORM_ACCEPTANCE
        contract.save(update_fields=[
            "purpose_text",
            "inputs_text",
            "outputs_text",
            "method_guidance_text",
            "acceptance_test_text",
        ])


def backwards(apps, schema_editor):
    return


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0035_projectplanningpurpose_pdo_fields"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
