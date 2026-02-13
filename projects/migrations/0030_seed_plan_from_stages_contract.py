# -*- coding: utf-8 -*-
from django.db import migrations


def seed_plan_from_stages_contract(apps, schema_editor):
    PhaseContract = apps.get_model("projects", "PhaseContract")

    if PhaseContract.objects.filter(key="PLAN_FROM_STAGES").exists():
        return

    PhaseContract.objects.create(
        key="PLAN_FROM_STAGES",
        title="Plan from stages",
        version=1,
        is_active=True,
        purpose_text=(
            "Derive a concrete plan (milestones, actions, risks) from PPDE stages."
        ),
        inputs_text=(
            "- Planning Purpose\n"
            "- Stage definitions (title, description, acceptance, deliverables, duration, risks)\n"
        ),
        outputs_text=(
            "JSON only:\n"
            "{\n"
            '  "milestones": [{"title": "...", "stage_title": "...", "acceptance_statement": "...", "target_date_hint": "..."}],\n'
            '  "actions": [{"title": "...", "stage_title": "...", "owner_role": "...", "definition_of_done": "...", "effort_hint": "..."}],\n'
            '  "risks": [{"title": "...", "stage_title": "...", "probability": "LOW|MED|HIGH", "impact": "LOW|MED|HIGH", "mitigation": "..."}]\n'
            "}\n"
        ),
        method_guidance_text=(
            "Work iteratively:\n"
            "- Identify gaps or ambiguities.\n"
            "- Ask concise clarification questions if needed.\n"
            "- Propose a revised draft.\n"
            "- Confirm with the user before finalising output.\n"
            "\n"
            "Keep all suggestions consistent with the project's stated goals, constraints, and acceptance criteria."
        ),
        acceptance_test_text=(
            "- Output is valid JSON only.\n"
            "- All items include a stage_title that matches a stage.\n"
            "- Milestones are verifiable.\n"
            "- Risks include mitigation."
        ),
        llm_review_prompt_text=(
            "Return JSON only. No prose.\n"
            "Ensure stage_title values match the input stage titles.\n"
        ),
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0029_projectplanningstage_key_variables"),
    ]

    operations = [
        migrations.RunPython(seed_plan_from_stages_contract, noop_reverse),
    ]
