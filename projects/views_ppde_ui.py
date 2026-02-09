# projects/views_ppde_ui.py
# PPDE (Planning WKO editor) UI

from __future__ import annotations

import json
from typing import Any, Dict, List

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from chats.services.llm import generate_panes
from projects.models import (
    PhaseContract,
    Project,
    ProjectCKO,
    ProjectPlanningAction,
    ProjectPlanningMilestone,
    ProjectPlanningRisk,
    ProjectPlanningPurpose,
    ProjectPlanningStage,
    ProjectWKO,
    ProjectExecutionTask,
)
from projects.services_project_membership import can_edit_ppde, is_project_committer


PPDE_VALIDATOR_BOILERPLATE = (
    "You are reviewing one PPDE block for clarity and completeness.\n"
    "\n"
    "Classify the block as exactly one:\n"
    "- PASS: clear, complete enough to lock.\n"
    "- WEAK: vague or underspecified; needs refinement.\n"
    "- CONFLICT: internally inconsistent or conflicts with the CKO seed context.\n"
    "\n"
    "Return OUTPUT as valid JSON only, matching this schema:\n"
    "{\n"
    '  "block_key": "string",\n'
    '  "verdict": "PASS | WEAK | CONFLICT",\n'
    '  "issues": ["string"],\n'
    '  "suggested_revision": "string",\n'
    '  "questions": ["string"],\n'
    '  "confidence": "LOW | MEDIUM | HIGH"\n'
    "}\n"
    "\n"
    "Rules:\n"
    "- No prose outside JSON in OUTPUT.\n"
    "- issues: empty if PASS.\n"
    "- questions: max 3, only if needed.\n"
    "- suggested_revision: provide a best improved rewrite even if WEAK/CONFLICT.\n"
)

PPDE_SEED_SUMMARY_BOILERPLATE = (
    "You are condensing a project's CKO seed context for PPDE display.\n"
    "Input will be a JSON object mapping labels to text.\n"
    "Return JSON only, with the same keys and shortened values.\n"
    "Guidelines:\n"
    "- Keep each value to 1-2 sentences or <= 200 characters.\n"
    "- Preserve meaning; do not invent new facts.\n"
    "- If a value is empty, keep it empty.\n"
)

PPDE_SEED_PURPOSE_BOILERPLATE = (
    "You are generating a Planning Purpose statement for PPDE from an accepted CKO.\n"
    "You MUST output JSON only. No prose.\n"
    "\n"
    "Return exactly this schema:\n"
    "{\n"
    '  "planning_purpose": "string"\n'
    "}\n"
    "\n"
    "Rules:\n"
    "- planning_purpose is 3 to 6 sentences.\n"
    "- It must state: audience, planning horizon, and optimisation focus.\n"
    "- It must NOT restate the project goal verbatim.\n"
    "- It must be plain language and execution-oriented.\n"
)

PPDE_STAGE_MAP_BOILERPLATE = (
    "You are generating a project stage map from an accepted CKO.\n"
    "You MUST output JSON only. No prose.\n"
    "\n"
    "Return an object matching this schema exactly:\n"
    "{\n"
    '  "stages": [\n'
    "    {\n"
    '      "title": "string",\n'
    '      "description": "string",\n'
    '      "purpose": "string",\n'
    '      "entry_condition": "string",\n'
    '      "acceptance_statement": "string",\n'
    '      "exit_condition": "string",\n'
    '      "key_deliverables": ["string"],\n'
    '      "duration_estimate": "string",\n'
    '      "risks_notes": "string"\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "\n"
    "Rules:\n"
    "- stages length: 3 to 8 is preferred; you may exceed only if necessary.\n"
    "- Each stage must be concrete and non-overlapping.\n"
    "- acceptance_statement must be verifiable.\n"
    "- key_deliverables must have at least 1 item.\n"
    "- duration_estimate is free text and may include deadlines.\n"
)

PPDE_PLAN_BOILERPLATE = (
    "You are deriving a concrete project plan from the provided stages.\n"
    "You MUST output JSON only. No prose.\n"
    "\n"
    "Return exactly this schema:\n"
    "{\n"
    '  "plan": {\n'
    '    "milestones": [\n'
    "      {\n"
    '        "title": "string",\n'
    '        "stage_title": "string",\n'
    '        "acceptance_statement": "string",\n'
    '        "target_date_hint": "string"\n'
    "      }\n"
    "    ],\n"
    '    "actions": [\n'
    "      {\n"
    '        "title": "string",\n'
    '        "stage_title": "string",\n'
    '        "owner_role": "string",\n'
    '        "definition_of_done": "string",\n'
    '        "effort_hint": "string"\n'
    "      }\n"
    "    ],\n"
    '    "risks": [\n'
    "      {\n"
    '        "title": "string",\n'
    '        "stage_title": "string",\n'
    '        "probability": "LOW|MED|HIGH",\n'
    '        "impact": "LOW|MED|HIGH",\n'
    '        "mitigation": "string"\n'
    "      }\n"
    "    ],\n"
    '    "assumptions": ["string"],\n'
    '    "dependencies": ["string"]\n'
    "  }\n"
    "}\n"
    "\n"
    "Rules:\n"
    "- Every milestone and action must map to a stage_title.\n"
    "- Milestones must be verifiable.\n"
    "- Provide 5-20 actions total.\n"
    "- Provide 3-12 risks total.\n"
    "- Keep items concise and practical.\n"
)

PPDE_STAGE_PLAN_BOILERPLATE = (
    "You are deriving plan items for a single stage.\n"
    "You MUST output JSON only. No prose.\n"
    "{\n"
    '  "milestones": [\n'
    "    {\n"
    '      "title": "string",\n'
    '      "stage_title": "string",\n'
    '      "acceptance_statement": "string",\n'
    '      "target_date_hint": "string"\n'
    "    }\n"
    "  ],\n"
    '  "actions": [\n'
    "    {\n"
    '      "title": "string",\n'
    '      "stage_title": "string",\n'
    '      "owner_role": "string",\n'
    '      "definition_of_done": "string",\n'
    '      "effort_hint": "string"\n'
    "    }\n"
    "  ],\n"
    '  "risks": [\n'
    "    {\n"
    '      "title": "string",\n'
    '      "stage_title": "string",\n'
    '      "probability": "LOW|MED|HIGH",\n'
    '      "impact": "LOW|MED|HIGH",\n'
    '      "mitigation": "string"\n'
    "    }\n"
    "  ]\n"
    "}\n"
)

def _ppde_help_key(project_id: int) -> str:
    return "ppde_help_log_" + str(project_id)


def _ppde_plan_preview_key(project_id: int) -> str:
    return "ppde_plan_preview_" + str(project_id)


def _ppde_stage_preview_key(project_id: int) -> str:
    return "ppde_stage_preview_" + str(project_id)


def _ppde_stage_edit_key(project_id: int) -> str:
    return "ppde_stage_edit_log_" + str(project_id)


def _get_ppde_help_log(request, project_id: int) -> List[Dict[str, str]]:
    return list(request.session.get(_ppde_help_key(project_id)) or [])


def _get_ppde_stage_edit_log(request, project_id: int) -> List[Dict[str, str]]:
    return list(request.session.get(_ppde_stage_edit_key(project_id)) or [])


def _ppde_help_answer(*, question: str, project: Project) -> str:
    system_blocks = [
        "- Explain intent and meaning of PPDE blocks and validation results.\n"
        "- Keep answers short and actionable.\n"
        "- If asked to change content, advise which block to edit.\n"
    ]
    user_text = "PPDE question:\n" + question.strip()
    panes = generate_panes(
        user_text,
        image_parts=None,
        system_blocks=system_blocks,
        force_model="gpt-5.1",
    )
    return (panes.get("output") or "").strip()


def _contract_text(contract: PhaseContract | None) -> str:
    if not contract:
        return ""
    parts = [
        "Contract: " + (contract.title or contract.key) + " ("
        + (contract.key or "") + " v" + str(contract.version) + ")",
        "Purpose:\n" + (contract.purpose_text or ""),
        "Inputs:\n" + (contract.inputs_text or ""),
        "Outputs:\n" + (contract.outputs_text or ""),
        "Method guidance:\n" + (contract.method_guidance_text or ""),
        "Acceptance test:\n" + (contract.acceptance_test_text or ""),
        "LLM review prompt:\n" + (contract.llm_review_prompt_text or ""),
    ]
    text = "\n\n".join(p for p in parts if p and p.strip())
    return text.strip()


def _parse_validation_json(raw_output: str, block_key: str) -> Dict[str, Any]:
    raw_output = (raw_output or "").strip()
    try:
        data = json.loads(raw_output)
    except Exception:
        return {
            "block_key": block_key,
            "verdict": "WEAK",
            "issues": ["OUTPUT was not valid JSON."],
            "suggested_revision": ("" if not raw_output else raw_output),
            "questions": ["Re-run verify; if persists, check prompt/contract."],
            "confidence": "LOW",
        }

    if not isinstance(data, dict):
        return {
            "block_key": block_key,
            "verdict": "WEAK",
            "issues": ["OUTPUT JSON was not an object."],
            "suggested_revision": "",
            "questions": ["Re-run verify; if persists, check prompt/contract."],
            "confidence": "LOW",
        }

    out: Dict[str, Any] = {}
    out["block_key"] = str(data.get("block_key") or block_key)

    verdict = str(data.get("verdict") or "").strip().upper()
    if verdict not in ("PASS", "WEAK", "CONFLICT"):
        verdict = "WEAK"
    out["verdict"] = verdict

    issues = data.get("issues")
    if isinstance(issues, list):
        out["issues"] = [str(x) for x in issues if str(x).strip()]
    else:
        out["issues"] = []

    out["suggested_revision"] = str(data.get("suggested_revision") or "")

    questions = data.get("questions")
    if isinstance(questions, list):
        qs = [str(x) for x in questions if str(x).strip()]
        out["questions"] = qs[:3]
    else:
        out["questions"] = []

    confidence = str(data.get("confidence") or "").strip().upper()
    if confidence not in ("LOW", "MEDIUM", "HIGH"):
        confidence = "LOW"
    out["confidence"] = confidence

    if out["verdict"] == "PASS":
        out["issues"] = []

    return out


def _seed_snapshot_from_cko(project: Project) -> Dict[str, Any]:
    if not project.defined_cko_id:
        return {}
    accepted = ProjectCKO.objects.filter(id=project.defined_cko_id, project=project).first()
    if not accepted or not isinstance(accepted.field_snapshot, dict):
        return {}
    raw = accepted.field_snapshot or {}
    if not isinstance(raw, dict):
        return {}
    seed_map = [
        ("Canonical summary", "canonical.summary"),
        ("Primary goal", "intent.primary_goal"),
        ("Success criteria", "intent.success_criteria"),
        ("In scope", "scope.in_scope"),
        ("Out of scope", "scope.out_of_scope"),
        ("Hard constraints", "scope.hard_constraints"),
    ]
    return {
        label: (raw.get(key) or "")
        for label, key in seed_map
        if (raw.get(key) or "").strip()
    }


def _parse_seed_summary_json(raw_output: str, seed_keys: List[str]) -> Dict[str, str]:
    raw_output = (raw_output or "").strip()
    try:
        data = json.loads(raw_output)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, str] = {}
    for k in seed_keys:
        v = data.get(k)
        if v is None:
            continue
        vv = str(v).strip()
        if vv:
            out[k] = vv
    return out


def _parse_planning_purpose_json(raw_output: str) -> tuple[str | None, str | None]:
    raw_output = (raw_output or "").strip()
    try:
        data = json.loads(raw_output)
    except Exception:
        return None, "Output was not valid JSON."
    if not isinstance(data, dict):
        return None, "Output JSON was not an object."
    purpose = str(data.get("planning_purpose") or "").strip()
    if not purpose:
        return None, "Missing planning_purpose."
    return purpose, None


def _ppde_validate_block(
    *,
    block_key: str,
    block_kind: str,
    payload: Dict[str, Any],
    seed_snapshot: Dict[str, Any],
    transform_contract: PhaseContract | None = None,
) -> Dict[str, Any]:
    seed_lines = []
    for k in sorted(seed_snapshot.keys()):
        v = (seed_snapshot.get(k) or "").strip()
        if not v:
            continue
        seed_lines.append("- " + k + ": " + v)
    seed_text = "Seeded CKO context:\n" + ("\n".join(seed_lines) if seed_lines else "(none)")

    if block_kind == "purpose":
        block_text = "Planning purpose:\n" + (payload.get("purpose_text") or "")
    else:
        block_text = (
            "Stage fields:\n"
            + "title: " + (payload.get("title") or "") + "\n"
            + "description: " + (payload.get("description") or "") + "\n"
            + "purpose: " + (payload.get("purpose") or "") + "\n"
            + "entry_condition: " + (payload.get("entry_condition") or "") + "\n"
            + "acceptance_statement: " + (payload.get("acceptance_statement") or "") + "\n"
            + "exit_condition: " + (payload.get("exit_condition") or "") + "\n"
            + "key_deliverables: " + ", ".join(payload.get("key_deliverables") or []) + "\n"
            + "duration_estimate: " + (payload.get("duration_estimate") or "") + "\n"
            + "risks_notes: " + (payload.get("risks_notes") or "")
        )

    user_text = "Block key: " + block_key + "\n" + seed_text + "\n\n" + block_text
    system_blocks = [PPDE_VALIDATOR_BOILERPLATE]
    contract_text = _contract_text(transform_contract)
    if contract_text:
        system_blocks.append(contract_text)

    panes = generate_panes(
        user_text,
        image_parts=None,
        system_blocks=system_blocks,
        force_model="gpt-5.1",
    )
    raw = str(panes.get("output") or "")
    out = _parse_validation_json(raw_output=raw, block_key=block_key)
    out["debug_user_text"] = user_text
    out["debug_system_blocks"] = [PPDE_VALIDATOR_BOILERPLATE]
    return out


def _parse_stage_map_json(raw_output: str) -> tuple[List[Dict[str, Any]], str | None]:
    raw_output = (raw_output or "").strip()
    try:
        data = json.loads(raw_output)
    except Exception:
        return [], "Output was not valid JSON."
    if not isinstance(data, dict):
        return [], "Output JSON was not an object."
    stages = data.get("stages")
    if not isinstance(stages, list):
        return [], "Missing stages list."

    out: List[Dict[str, Any]] = []
    for item in stages:
        if not isinstance(item, dict):
            continue
        key_deliverables = item.get("key_deliverables")
        if isinstance(key_deliverables, list):
            kd = [str(x).strip() for x in key_deliverables if str(x).strip()]
        else:
            kd = []
        if not kd:
            kd = ["TBD"]
        out.append(
            {
                "title": str(item.get("title") or "").strip(),
                "description": str(item.get("description") or "").strip(),
                "purpose": str(item.get("purpose") or "").strip(),
                "entry_condition": str(item.get("entry_condition") or "").strip(),
                "acceptance_statement": str(item.get("acceptance_statement") or "").strip(),
                "exit_condition": str(item.get("exit_condition") or "").strip(),
                "key_deliverables": kd,
                "duration_estimate": str(item.get("duration_estimate") or "").strip(),
                "risks_notes": str(item.get("risks_notes") or "").strip(),
            }
        )
    if not out:
        return [], "No valid stages returned."
    return out, None


def _parse_plan_json(raw_output: str, stage_titles: List[str]) -> tuple[Dict[str, Any], List[str]]:
    warnings: List[str] = []
    raw_output = (raw_output or "").strip()
    try:
        data = json.loads(raw_output)
    except Exception:
        return {}, ["Output was not valid JSON."]
    if not isinstance(data, dict):
        return {}, ["Output JSON was not an object."]
    plan = data.get("plan") if isinstance(data.get("plan"), dict) else None
    if not isinstance(plan, dict):
        return {}, ["Missing plan object."]

    def norm_stage_title(val: str) -> str:
        return val.strip()

    milestones = plan.get("milestones") if isinstance(plan.get("milestones"), list) else []
    actions = plan.get("actions") if isinstance(plan.get("actions"), list) else []
    risks = plan.get("risks") if isinstance(plan.get("risks"), list) else []
    assumptions = plan.get("assumptions") if isinstance(plan.get("assumptions"), list) else []
    dependencies = plan.get("dependencies") if isinstance(plan.get("dependencies"), list) else []

    def fix_prob(val: str) -> str:
        v = (val or "").strip().upper()
        if v not in ("LOW", "MED", "HIGH"):
            return "MED"
        return v

    def stage_ok(val: str) -> str:
        st = norm_stage_title(val or "")
        if st and st not in stage_titles:
            warnings.append("Stage title not found: " + st)
            return ""
        return st

    out_plan = {
        "milestones": [],
        "actions": [],
        "risks": [],
        "assumptions": [str(x).strip() for x in assumptions if str(x).strip()],
        "dependencies": [str(x).strip() for x in dependencies if str(x).strip()],
    }

    for item in milestones:
        if not isinstance(item, dict):
            continue
        out_plan["milestones"].append(
            {
                "title": str(item.get("title") or "").strip(),
                "stage_title": stage_ok(item.get("stage_title") or ""),
                "acceptance_statement": str(item.get("acceptance_statement") or "").strip(),
                "target_date_hint": str(item.get("target_date_hint") or "").strip(),
            }
        )

    for item in actions:
        if not isinstance(item, dict):
            continue
        out_plan["actions"].append(
            {
                "title": str(item.get("title") or "").strip(),
                "stage_title": stage_ok(item.get("stage_title") or ""),
                "owner_role": str(item.get("owner_role") or "").strip(),
                "definition_of_done": str(item.get("definition_of_done") or "").strip(),
                "effort_hint": str(item.get("effort_hint") or "").strip(),
            }
        )

    for item in risks:
        if not isinstance(item, dict):
            continue
        out_plan["risks"].append(
            {
                "title": str(item.get("title") or "").strip(),
                "stage_title": stage_ok(item.get("stage_title") or ""),
                "probability": fix_prob(item.get("probability") or ""),
                "impact": fix_prob(item.get("impact") or ""),
                "mitigation": str(item.get("mitigation") or "").strip(),
            }
        )

    return out_plan, warnings


def _summarize_plan(plan_out: Dict[str, Any]) -> Dict[str, Any]:
    def summarize_items(items: List[Dict[str, Any]]) -> Dict[str, Any]:
        rows = []
        for item in items[:3]:
            title = str(item.get("title") or "").strip()
            stage = str(item.get("stage_title") or "").strip()
            label = title or "(untitled)"
            if stage:
                label += " — " + stage
            rows.append(label)
        return {
            "count": len(items),
            "top": rows,
        }

    milestones = plan_out.get("milestones") if isinstance(plan_out.get("milestones"), list) else []
    actions = plan_out.get("actions") if isinstance(plan_out.get("actions"), list) else []
    risks = plan_out.get("risks") if isinstance(plan_out.get("risks"), list) else []
    return {
        "milestones": summarize_items(milestones),
        "actions": summarize_items(actions),
        "risks": summarize_items(risks),
    }


def _summarize_stages(stages_out: List[Dict[str, Any]]) -> Dict[str, Any]:
    items = []
    for stage in stages_out[:8]:
        title = (stage.get("title") or "").strip()
        desc = (stage.get("description") or "").strip()
        items.append(
            {
                "title": title or "(untitled)",
                "description": desc,
            }
        )
    return {
        "count": len(stages_out),
        "items": items,
    }


def _apply_stages_to_draft(*, project: Project, stages_out: List[Dict[str, Any]]) -> None:
    ProjectPlanningStage.objects.filter(project=project).delete()
    for idx, stage_data in enumerate(stages_out, start=1):
        ProjectPlanningStage.objects.create(
            project=project,
            order_index=idx,
            title=stage_data.get("title", ""),
            description=stage_data.get("description", ""),
            purpose=stage_data.get("purpose", ""),
            entry_condition=stage_data.get("entry_condition", ""),
            acceptance_statement=stage_data.get("acceptance_statement", ""),
            exit_condition=stage_data.get("exit_condition", ""),
            key_deliverables=stage_data.get("key_deliverables", []),
            duration_estimate=stage_data.get("duration_estimate", ""),
            risks_notes=stage_data.get("risks_notes", ""),
            status=ProjectPlanningStage.Status.DRAFT,
            proposed_by=None,
            proposed_at=None,
            locked_by=None,
            locked_at=None,
            last_validation={},
        )


def _apply_plan_to_draft(*, project: Project, plan_out: Dict[str, Any]) -> None:
    ProjectPlanningMilestone.objects.filter(project=project).delete()
    ProjectPlanningAction.objects.filter(project=project).delete()
    ProjectPlanningRisk.objects.filter(project=project).delete()

    for idx, item in enumerate(plan_out.get("milestones") or [], start=1):
        ProjectPlanningMilestone.objects.create(
            project=project,
            order_index=idx,
            title=item.get("title", ""),
            stage_title=item.get("stage_title", ""),
            acceptance_statement=item.get("acceptance_statement", ""),
            target_date_hint=item.get("target_date_hint", ""),
            status=ProjectPlanningMilestone.Status.DRAFT,
        )
    for idx, item in enumerate(plan_out.get("actions") or [], start=1):
        ProjectPlanningAction.objects.create(
            project=project,
            order_index=idx,
            title=item.get("title", ""),
            stage_title=item.get("stage_title", ""),
            owner_role=item.get("owner_role", ""),
            definition_of_done=item.get("definition_of_done", ""),
            effort_hint=item.get("effort_hint", ""),
            status=ProjectPlanningAction.Status.DRAFT,
        )
    for idx, item in enumerate(plan_out.get("risks") or [], start=1):
        ProjectPlanningRisk.objects.create(
            project=project,
            order_index=idx,
            title=item.get("title", ""),
            stage_title=item.get("stage_title", ""),
            probability=item.get("probability", ""),
            impact=item.get("impact", ""),
            mitigation=item.get("mitigation", ""),
            status=ProjectPlanningRisk.Status.DRAFT,
        )


def _ensure_ppde_blocks(project: Project) -> tuple[ProjectPlanningPurpose, List[ProjectPlanningStage]]:
    purpose, _ = ProjectPlanningPurpose.objects.get_or_create(project=project)
    stages = list(ProjectPlanningStage.objects.filter(project=project).order_by("order_index", "id"))
    if not stages:
        stages = [
            ProjectPlanningStage.objects.create(
                project=project,
                order_index=1,
                title="Stage 1",
            )
        ]
    return purpose, stages


def _stage_payload_from_request(request) -> Dict[str, Any]:
    key_lines = (request.POST.get("key_deliverables") or "").splitlines()
    key_deliverables = [line.strip() for line in key_lines if line.strip()]
    return {
        "title": (request.POST.get("title") or "").strip(),
        "description": (request.POST.get("description") or "").strip(),
        "purpose": (request.POST.get("purpose") or "").strip(),
        "entry_condition": (request.POST.get("entry_condition") or "").strip(),
        "acceptance_statement": (request.POST.get("acceptance_statement") or "").strip(),
        "exit_condition": (request.POST.get("exit_condition") or "").strip(),
        "key_deliverables": key_deliverables,
        "duration_estimate": (request.POST.get("duration_estimate") or "").strip(),
        "risks_notes": (request.POST.get("risks_notes") or "").strip(),
    }


def _stage_payload_from_post(request, stage_id: int) -> Dict[str, Any]:
    prefix = "stage_" + str(stage_id) + "__"
    key_lines = (request.POST.get(prefix + "key_deliverables") or "").splitlines()
    key_deliverables = [line.strip() for line in key_lines if line.strip()]
    return {
        "title": (request.POST.get(prefix + "title") or "").strip(),
        "description": (request.POST.get(prefix + "description") or "").strip(),
        "purpose": (request.POST.get(prefix + "purpose") or "").strip(),
        "entry_condition": (request.POST.get(prefix + "entry_condition") or "").strip(),
        "acceptance_statement": (request.POST.get(prefix + "acceptance_statement") or "").strip(),
        "exit_condition": (request.POST.get(prefix + "exit_condition") or "").strip(),
        "key_deliverables": key_deliverables,
        "duration_estimate": (request.POST.get(prefix + "duration_estimate") or "").strip(),
        "risks_notes": (request.POST.get(prefix + "risks_notes") or "").strip(),
    }


def _stage_payload_from_model(stage: ProjectPlanningStage) -> Dict[str, Any]:
    return {
        "title": (stage.title or "").strip(),
        "description": (stage.description or "").strip(),
        "purpose": (stage.purpose or "").strip(),
        "entry_condition": (stage.entry_condition or "").strip(),
        "acceptance_statement": (stage.acceptance_statement or "").strip(),
        "exit_condition": (stage.exit_condition or "").strip(),
        "key_deliverables": list(stage.key_deliverables or []),
        "duration_estimate": (stage.duration_estimate or "").strip(),
        "risks_notes": (stage.risks_notes or "").strip(),
    }


def _stage_payload_changed(stage: ProjectPlanningStage, payload: Dict[str, Any]) -> bool:
    prior = _stage_payload_from_model(stage)
    if prior.get("key_deliverables") != payload.get("key_deliverables"):
        return True
    for key in prior:
        if key == "key_deliverables":
            continue
        if (prior.get(key) or "") != (payload.get(key) or ""):
            return True
    return False


def _block_anchor(block_key: str) -> str:
    if block_key == "purpose":
        return "#ppde-purpose"
    if block_key.startswith("stage:"):
        stage_id = block_key.split(":", 1)[1]
        return "#ppde-stage-" + stage_id
    return ""


@login_required
def ppde_detail(request, project_id: int) -> HttpResponse:
    project = get_object_or_404(Project, id=project_id)
    if not can_edit_ppde(project, request.user):
        messages.error(request, "You do not have permission to edit this project.")
        return redirect("accounts:dashboard")

    if not project.defined_cko_id:
        messages.error(request, "Project is not defined yet. Complete PDE first.")
        return redirect("projects:pde_detail", project_id=project.id)

    can_commit = is_project_committer(project, request.user)
    purpose, stages = _ensure_ppde_blocks(project)
    seed_snapshot = _seed_snapshot_from_cko(project)
    structure_contract = PhaseContract.objects.filter(key="STRUCTURE_PROJECT", is_active=True).first()
    transform_contract = PhaseContract.objects.filter(key="TRANSFORM_STAGE", is_active=True).first()
    plan_contract = PhaseContract.objects.filter(key="PLAN_FROM_STAGES", is_active=True).first()

    action = (request.POST.get("action") or "").strip().lower()
    block_key = (request.POST.get("block_key") or "").strip()
    anchor = _block_anchor(block_key)

    all_locked = (
        purpose.status == ProjectPlanningPurpose.Status.PASS_LOCKED
        and all(s.status == ProjectPlanningStage.Status.PASS_LOCKED for s in stages)
    )
    has_proposed = (
        purpose.status == ProjectPlanningPurpose.Status.PROPOSED
        or any(s.status == ProjectPlanningStage.Status.PROPOSED for s in stages)
    )

    if request.method == "POST" and action != "verify_block":
        if request.session.get("ppde_last_validation_key"):
            request.session.pop("ppde_last_validation_key", None)
            request.session.modified = True

    if request.method == "POST" and action == "help_ask":
        question = (request.POST.get("help_question") or "").strip()
        if not question:
            messages.error(request, "Question is required.")
            return redirect("projects:ppde_detail", project_id=project.id)
        help_log = _get_ppde_help_log(request, project.id)
        help_log.append({"role": "user", "text": question})
        answer = _ppde_help_answer(question=question, project=project)
        help_log.append({"role": "assistant", "text": answer})
        request.session[_ppde_help_key(project.id)] = help_log[-20:]
        request.session["ppde_help_auto_open_" + str(project.id)] = True
        request.session.modified = True
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "help_clear":
        request.session[_ppde_help_key(project.id)] = []
        request.session.modified = True
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "seed_toggle":
        view_mode = (request.POST.get("seed_view") or "").strip().lower()
        if view_mode in ("short", "full"):
            request.session["ppde_seed_view"] = view_mode
            request.session.modified = True
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "seed_condense":
        if not can_commit:
            messages.error(request, "Only the Project Committer can update seed summaries.")
            return redirect("projects:ppde_detail", project_id=project.id)
        seed_snapshot = _seed_snapshot_from_cko(project)
        if not seed_snapshot:
            messages.error(request, "No seed context available to condense.")
            return redirect("projects:ppde_detail", project_id=project.id)
        seed_keys = list(seed_snapshot.keys())
        user_text = "Seed context JSON:\n" + json.dumps(seed_snapshot, indent=2, ensure_ascii=True)
        panes = generate_panes(
            user_text,
            image_parts=None,
            system_blocks=[PPDE_SEED_SUMMARY_BOILERPLATE],
            force_model="gpt-5.1",
        )
        summary = _parse_seed_summary_json(str(panes.get("output") or ""), seed_keys)
        if not summary:
            messages.error(request, "Seed summary failed. Try again.")
            return redirect("projects:ppde_detail", project_id=project.id)
        project.ppde_seed_summary = summary
        project.save(update_fields=["ppde_seed_summary", "updated_at"])
        request.session["ppde_seed_view"] = "short"
        request.session.modified = True
        messages.success(request, "Seed context condensed.")
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "stage_edit_ask":
        if not can_commit:
            messages.error(request, "Only the Project Committer can edit stage previews.")
            return redirect("projects:ppde_detail", project_id=project.id)
        if not structure_contract:
            messages.error(request, "No active STRUCTURE_PROJECT contract configured. Contact administrator.")
            return redirect("projects:ppde_detail", project_id=project.id)

        stage_preview = request.session.get(_ppde_stage_preview_key(project.id)) or {}
        stages_out = stage_preview.get("stages") if isinstance(stage_preview, dict) else None
        if not isinstance(stages_out, list):
            messages.error(request, "No stage preview available to edit.")
            return redirect("projects:ppde_detail", project_id=project.id)

        question = (request.POST.get("stage_edit_question") or "").strip()
        if not question:
            messages.error(request, "Question is required.")
            return redirect("projects:ppde_detail", project_id=project.id)

        edit_log = _get_ppde_stage_edit_log(request, project.id)
        edit_log.append({"role": "user", "text": question})

        user_text = (
            "CURRENT_STAGE_MAP_JSON:\n"
            + json.dumps({"stages": stages_out}, indent=2, ensure_ascii=True)
            + "\n\nREQUEST:\n"
            + question
        )
        contract_text = _contract_text(structure_contract)
        panes = generate_panes(
            user_text,
            image_parts=None,
            system_blocks=[t for t in [contract_text, PPDE_STAGE_MAP_BOILERPLATE] if t],
            force_model="gpt-5.1",
        )
        stages_updated, err = _parse_stage_map_json(str(panes.get("output") or ""))
        if err:
            messages.error(request, "Stage edit failed: " + err)
            return redirect("projects:ppde_detail", project_id=project.id)

        request.session[_ppde_stage_preview_key(project.id)] = {
            "stages": stages_updated,
            "summary": _summarize_stages(stages_updated),
        }
        edit_log.append(
            {
                "role": "assistant",
                "text": f"Updated preview with {len(stages_updated)} stages.",
            }
        )
        request.session[_ppde_stage_edit_key(project.id)] = edit_log[-20:]
        request.session["ppde_stage_edit_auto_open_" + str(project.id)] = True
        request.session.modified = True

        messages.success(request, "Stage preview updated.")
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "stage_edit_clear":
        request.session[_ppde_stage_edit_key(project.id)] = []
        request.session.pop("ppde_stage_edit_auto_open_" + str(project.id), None)
        request.session.modified = True
        messages.info(request, "Stage edit chat cleared.")
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "seed_purpose_from_cko":
        if not can_commit:
            messages.error(request, "Only the Project Committer can seed planning purpose.")
            return redirect("projects:ppde_detail", project_id=project.id)
        if not structure_contract:
            messages.error(request, "No active STRUCTURE_PROJECT contract configured. Contact administrator.")
            return redirect("projects:ppde_detail", project_id=project.id)
        if (purpose.value_text or "").strip():
            messages.warning(request, "Planning purpose already set; seed skipped.")
            return redirect("projects:ppde_detail", project_id=project.id)

        accepted = ProjectCKO.objects.filter(id=project.defined_cko_id, project=project).first()
        full_snapshot = accepted.field_snapshot if accepted and isinstance(accepted.field_snapshot, dict) else {}
        if not full_snapshot:
            messages.error(request, "No accepted CKO snapshot available.")
            return redirect("projects:ppde_detail", project_id=project.id)

        user_text = "ACCEPTED_CKO_SNAPSHOT:\n" + json.dumps(full_snapshot, indent=2, ensure_ascii=True)
        if project.ppde_seed_summary:
            user_text += "\n\nSEED_SUMMARY:\n" + json.dumps(project.ppde_seed_summary, indent=2, ensure_ascii=True)
        user_text += "\n\nINSTRUCTION:\nGenerate planning_purpose only."

        contract_text = _contract_text(structure_contract)
        panes = generate_panes(
            user_text,
            image_parts=None,
            system_blocks=[t for t in [contract_text, PPDE_SEED_PURPOSE_BOILERPLATE] if t],
            force_model="gpt-5.1",
        )
        purpose_text, err = _parse_planning_purpose_json(str(panes.get("output") or ""))
        if err:
            messages.error(request, "Seed planning purpose failed: " + err)
            return redirect("projects:ppde_detail", project_id=project.id)

        purpose.value_text = purpose_text
        purpose.status = ProjectPlanningPurpose.Status.DRAFT
        purpose.proposed_by = None
        purpose.proposed_at = None
        purpose.locked_by = None
        purpose.locked_at = None
        purpose.last_validation = {}
        purpose.last_edited_by = request.user
        purpose.last_edited_at = timezone.now()
        purpose.save(
            update_fields=[
                "value_text",
                "status",
                "proposed_by",
                "proposed_at",
                "locked_by",
                "locked_at",
                "last_validation",
                "last_edited_by",
                "last_edited_at",
                "updated_at",
            ]
        )

        messages.success(request, "Planning purpose seeded from CKO.")
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "generate_from_cko":
        if not can_commit:
            messages.error(request, "Only the Project Committer can generate stages.")
            return redirect("projects:ppde_detail", project_id=project.id)
        if not structure_contract:
            messages.error(request, "No active contract configured. Contact administrator.")
            return redirect("projects:ppde_detail", project_id=project.id)
        if not seed_snapshot:
            messages.error(request, "No accepted CKO snapshot available.")
            return redirect("projects:ppde_detail", project_id=project.id)

        contract_text = _contract_text(structure_contract)
        accepted = ProjectCKO.objects.filter(id=project.defined_cko_id, project=project).first()
        full_snapshot = accepted.field_snapshot if accepted and isinstance(accepted.field_snapshot, dict) else {}
        user_text = "Seed context JSON:\n" + json.dumps(seed_snapshot, indent=2, ensure_ascii=True)
        if full_snapshot:
            user_text += "\n\nFull CKO snapshot JSON:\n" + json.dumps(full_snapshot, indent=2, ensure_ascii=True)
        panes = generate_panes(
            user_text,
            image_parts=None,
            system_blocks=[t for t in [contract_text, PPDE_STAGE_MAP_BOILERPLATE] if t],
            force_model="gpt-5.1",
        )
        stages_out, err = _parse_stage_map_json(str(panes.get("output") or ""))
        if err:
            messages.error(request, "Stage generation failed: " + err)
            return redirect("projects:ppde_detail", project_id=project.id)
        request.session[_ppde_stage_preview_key(project.id)] = {
            "stages": stages_out,
            "summary": _summarize_stages(stages_out),
        }
        request.session.modified = True

        messages.success(request, f"Generated {len(stages_out)} stages. Review the preview before applying.")
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "stage_preview_apply":
        if not can_commit:
            messages.error(request, "Only the Project Committer can apply stages.")
            return redirect("projects:ppde_detail", project_id=project.id)
        payload = request.session.get(_ppde_stage_preview_key(project.id)) or {}
        stages_out = payload.get("stages") if isinstance(payload, dict) else None
        if not isinstance(stages_out, list):
            messages.error(request, "No stage preview available.")
            return redirect("projects:ppde_detail", project_id=project.id)

        _apply_stages_to_draft(project=project, stages_out=stages_out)

        purpose.status = ProjectPlanningPurpose.Status.DRAFT
        purpose.proposed_by = None
        purpose.proposed_at = None
        purpose.locked_by = None
        purpose.locked_at = None
        purpose.last_validation = {}
        purpose.save(
            update_fields=[
                "status",
                "proposed_by",
                "proposed_at",
                "locked_by",
                "locked_at",
                "last_validation",
                "updated_at",
            ]
        )

        request.session.pop(_ppde_stage_preview_key(project.id), None)
        request.session.modified = True

        apply_mode = (request.POST.get("apply_mode") or "").strip().lower()
        if apply_mode == "edit":
            messages.success(request, "Stages applied to draft for editing.")
        else:
            messages.success(request, "Stages applied.")
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "stage_preview_discard":
        request.session.pop(_ppde_stage_preview_key(project.id), None)
        request.session.modified = True
        messages.info(request, "Stage preview discarded.")
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "generate_plan_from_stages":
        if not can_commit:
            messages.error(request, "Only the Project Committer can generate the plan.")
            return redirect("projects:ppde_detail", project_id=project.id)
        if not plan_contract:
            messages.error(request, "No active contract configured. Contact administrator.")
            return redirect("projects:ppde_detail", project_id=project.id)
        stages_payload = list(ProjectPlanningStage.objects.filter(project=project).order_by("order_index", "id"))
        stage_titles = [s.title.strip() for s in stages_payload if (s.title or "").strip()]
        if not stages_payload:
            messages.error(request, "No stages available to generate a plan.")
            return redirect("projects:ppde_detail", project_id=project.id)

        user_text = "Planning purpose:\n" + (purpose.value_text or "").strip() + "\n\nStages:\n"
        for s in stages_payload:
            user_text += (
                "- title: " + (s.title or "") + "\n"
                + "  description: " + (s.description or "") + "\n"
                + "  acceptance_statement: " + (s.acceptance_statement or "") + "\n"
                + "  key_deliverables: " + ", ".join(s.key_deliverables or []) + "\n"
                + "  duration_estimate: " + (s.duration_estimate or "") + "\n"
                + "  risks_notes: " + (s.risks_notes or "") + "\n"
            )

        contract_text = _contract_text(plan_contract)
        panes = generate_panes(
            user_text,
            image_parts=None,
            system_blocks=[t for t in [contract_text, PPDE_PLAN_BOILERPLATE] if t],
            force_model="gpt-5.1",
        )
        plan_out, warn_list = _parse_plan_json(str(panes.get("output") or ""), stage_titles)
        if not plan_out:
            messages.error(request, "Plan generation failed. Check the contract or try again.")
            return redirect("projects:ppde_detail", project_id=project.id)

        request.session[_ppde_plan_preview_key(project.id)] = {
            "plan": plan_out,
            "summary": _summarize_plan(plan_out),
            "warnings": warn_list,
        }
        request.session.modified = True

        for w in warn_list:
            messages.warning(request, w)
        messages.success(request, "Plan generated. Review the summary before applying.")
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "plan_preview_apply":
        if not can_commit:
            messages.error(request, "Only the Project Committer can apply the plan.")
            return redirect("projects:ppde_detail", project_id=project.id)
        payload = request.session.get(_ppde_plan_preview_key(project.id)) or {}
        plan_out = payload.get("plan") if isinstance(payload, dict) else None
        if not isinstance(plan_out, dict):
            messages.error(request, "No plan preview available.")
            return redirect("projects:ppde_detail", project_id=project.id)

        _apply_plan_to_draft(project=project, plan_out=plan_out)
        request.session.pop(_ppde_plan_preview_key(project.id), None)
        request.session.modified = True

        apply_mode = (request.POST.get("apply_mode") or "").strip().lower()
        if apply_mode == "edit":
            messages.success(request, "Plan applied to draft for editing.")
        else:
            messages.success(request, "Plan applied.")
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "plan_preview_discard":
        request.session.pop(_ppde_plan_preview_key(project.id), None)
        request.session.modified = True
        messages.info(request, "Plan preview discarded.")
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "derive_stage_plan":
        if not plan_contract:
            messages.error(request, "No active contract configured. Contact administrator.")
            return redirect("projects:ppde_detail", project_id=project.id)
        if not block_key.startswith("stage:"):
            messages.error(request, "Missing stage key.")
            return redirect("projects:ppde_detail", project_id=project.id)
        stage_id = block_key.split(":", 1)[1]
        stage = get_object_or_404(ProjectPlanningStage, id=stage_id, project=project)
        stage_title = (stage.title or "").strip()

        user_text = (
            "Planning purpose:\n" + (purpose.value_text or "").strip() + "\n\n"
            "Stage:\n"
            "title: " + (stage.title or "") + "\n"
            "description: " + (stage.description or "") + "\n"
            "acceptance_statement: " + (stage.acceptance_statement or "") + "\n"
            "key_deliverables: " + ", ".join(stage.key_deliverables or []) + "\n"
            "duration_estimate: " + (stage.duration_estimate or "") + "\n"
            "risks_notes: " + (stage.risks_notes or "") + "\n"
        )
        contract_text = _contract_text(plan_contract)
        panes = generate_panes(
            user_text,
            image_parts=None,
            system_blocks=[t for t in [contract_text, PPDE_STAGE_PLAN_BOILERPLATE] if t],
            force_model="gpt-5.1",
        )
        raw = str(panes.get("output") or "")
        try:
            data = json.loads(raw)
        except Exception:
            messages.error(request, "Stage plan derivation failed: invalid JSON.")
            return redirect("projects:ppde_detail", project_id=project.id)
        if not isinstance(data, dict):
            messages.error(request, "Stage plan derivation failed: invalid data.")
            return redirect("projects:ppde_detail", project_id=project.id)

        milestones = data.get("milestones") if isinstance(data.get("milestones"), list) else []
        actions = data.get("actions") if isinstance(data.get("actions"), list) else []
        risks = data.get("risks") if isinstance(data.get("risks"), list) else []

        ProjectPlanningMilestone.objects.filter(project=project, stage=stage).delete()
        ProjectPlanningAction.objects.filter(project=project, stage=stage).delete()
        ProjectPlanningRisk.objects.filter(project=project, stage=stage).delete()

        status = ProjectPlanningMilestone.Status.PROPOSED
        for idx, item in enumerate(milestones, start=1):
            if not isinstance(item, dict):
                continue
            ProjectPlanningMilestone.objects.create(
                project=project,
                stage=stage,
                order_index=idx,
                title=str(item.get("title") or ""),
                stage_title=str(item.get("stage_title") or stage_title),
                acceptance_statement=str(item.get("acceptance_statement") or ""),
                target_date_hint=str(item.get("target_date_hint") or ""),
                status=status,
                proposed_by=request.user,
                proposed_at=timezone.now(),
            )
        for idx, item in enumerate(actions, start=1):
            if not isinstance(item, dict):
                continue
            ProjectPlanningAction.objects.create(
                project=project,
                stage=stage,
                order_index=idx,
                title=str(item.get("title") or ""),
                stage_title=str(item.get("stage_title") or stage_title),
                owner_role=str(item.get("owner_role") or ""),
                definition_of_done=str(item.get("definition_of_done") or ""),
                effort_hint=str(item.get("effort_hint") or ""),
                status=ProjectPlanningAction.Status.PROPOSED,
                proposed_by=request.user,
                proposed_at=timezone.now(),
            )
        for idx, item in enumerate(risks, start=1):
            if not isinstance(item, dict):
                continue
            ProjectPlanningRisk.objects.create(
                project=project,
                stage=stage,
                order_index=idx,
                title=str(item.get("title") or ""),
                stage_title=str(item.get("stage_title") or stage_title),
                probability=str(item.get("probability") or ""),
                impact=str(item.get("impact") or ""),
                mitigation=str(item.get("mitigation") or ""),
                status=ProjectPlanningRisk.Status.PROPOSED,
                proposed_by=request.user,
                proposed_at=timezone.now(),
            )
        messages.success(request, "Derived plan items for stage.")
        return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

    if request.method == "POST" and action == "approve_stage_plan":
        if not can_commit:
            messages.error(request, "Only the Project Committer can approve.")
            return redirect("projects:ppde_detail", project_id=project.id)
        if not block_key.startswith("stage:"):
            messages.error(request, "Missing stage key.")
            return redirect("projects:ppde_detail", project_id=project.id)
        stage_id = block_key.split(":", 1)[1]
        stage = get_object_or_404(ProjectPlanningStage, id=stage_id, project=project)

        ProjectPlanningMilestone.objects.filter(project=project, stage=stage, status=ProjectPlanningMilestone.Status.PROPOSED).update(
            status=ProjectPlanningMilestone.Status.PASS_LOCKED,
            locked_by=request.user,
            locked_at=timezone.now(),
        )
        ProjectPlanningAction.objects.filter(project=project, stage=stage, status=ProjectPlanningAction.Status.PROPOSED).update(
            status=ProjectPlanningAction.Status.PASS_LOCKED,
            locked_by=request.user,
            locked_at=timezone.now(),
        )
        ProjectPlanningRisk.objects.filter(project=project, stage=stage, status=ProjectPlanningRisk.Status.PROPOSED).update(
            status=ProjectPlanningRisk.Status.PASS_LOCKED,
            locked_by=request.user,
            locked_at=timezone.now(),
        )
        messages.success(request, "Stage plan items approved.")
        return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

    if request.method == "POST" and action == "verify_all":
        if not transform_contract:
            messages.error(request, "No active contract configured. Contact administrator.")
            return redirect("projects:ppde_detail", project_id=project.id)
        purpose_payload = {"purpose_text": (purpose.value_text or "").strip()}
        purpose.last_validation = _ppde_validate_block(
            block_key="purpose",
            block_kind="purpose",
            payload=purpose_payload,
            seed_snapshot=seed_snapshot,
            transform_contract=transform_contract,
        )
        purpose.save(update_fields=["last_validation", "updated_at"])

        for stage in stages:
            payload = _stage_payload_from_model(stage)
            stage.last_validation = _ppde_validate_block(
                block_key="stage:" + str(stage.id),
                block_kind="stage",
                payload=payload,
                seed_snapshot=seed_snapshot,
                transform_contract=transform_contract,
            )
            stage.save(update_fields=["last_validation", "updated_at"])

        request.session["ppde_last_validation_key"] = "purpose"
        request.session.modified = True
        messages.info(request, "Verification complete for all blocks.")
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "verify_propose_all":
        if not transform_contract:
            messages.error(request, "No active contract configured. Contact administrator.")
            return redirect("projects:ppde_detail", project_id=project.id)
        purpose_payload = {"purpose_text": (purpose.value_text or "").strip()}
        purpose.last_validation = _ppde_validate_block(
            block_key="purpose",
            block_kind="purpose",
            payload=purpose_payload,
            seed_snapshot=seed_snapshot,
            transform_contract=transform_contract,
        )
        purpose.save(update_fields=["last_validation", "updated_at"])

        for stage in stages:
            payload = _stage_payload_from_model(stage)
            stage.last_validation = _ppde_validate_block(
                block_key="stage:" + str(stage.id),
                block_kind="stage",
                payload=payload,
                seed_snapshot=seed_snapshot,
                transform_contract=transform_contract,
            )
            stage.save(update_fields=["last_validation", "updated_at"])

        if purpose.status == ProjectPlanningPurpose.Status.DRAFT:
            purpose.status = ProjectPlanningPurpose.Status.PROPOSED
            purpose.proposed_by = request.user
            purpose.proposed_at = timezone.now()
            purpose.save(update_fields=["status", "proposed_by", "proposed_at", "updated_at"])

        for stage in stages:
            if stage.status != ProjectPlanningStage.Status.DRAFT:
                continue
            stage.status = ProjectPlanningStage.Status.PROPOSED
            stage.proposed_by = request.user
            stage.proposed_at = timezone.now()
            stage.save(update_fields=["status", "proposed_by", "proposed_at", "updated_at"])

        request.session["ppde_last_validation_key"] = "purpose"
        request.session.modified = True
        messages.success(request, "Verification complete. All draft blocks proposed.")
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "save_exit":
        messages.info(request, "Exited PPDE. Use per-block Save Draft to persist changes.")
        return redirect("accounts:project_config_info", project_id=project.id)

    if request.method == "POST" and action == "save_all":
        changed_blocks = 0
        purpose_text = (request.POST.get("purpose_text") or "").strip()
        if purpose_text:
            prior_text = (purpose.value_text or "").strip()
            if purpose_text != prior_text:
                if can_commit and purpose.status in (ProjectPlanningPurpose.Status.PROPOSED, ProjectPlanningPurpose.Status.PASS_LOCKED):
                    purpose.status = ProjectPlanningPurpose.Status.DRAFT
                    purpose.proposed_by = None
                    purpose.proposed_at = None
                    purpose.locked_by = None
                    purpose.locked_at = None
                    purpose.last_validation = {}
                purpose.value_text = purpose_text
                purpose.last_edited_by = request.user
                purpose.last_edited_at = timezone.now()
                purpose.save(
                    update_fields=[
                        "value_text",
                        "last_edited_by",
                        "last_edited_at",
                        "status",
                        "proposed_by",
                        "proposed_at",
                        "locked_by",
                        "locked_at",
                        "last_validation",
                        "updated_at",
                    ]
                )
                changed_blocks += 1

        for stage in stages:
            payload = _stage_payload_from_post(request, stage.id)
            if not any(payload.values()):
                continue
            changed = _stage_payload_changed(stage, payload)
            if not changed:
                continue
            if can_commit and stage.status in (ProjectPlanningStage.Status.PROPOSED, ProjectPlanningStage.Status.PASS_LOCKED):
                stage.status = ProjectPlanningStage.Status.DRAFT
                stage.proposed_by = None
                stage.proposed_at = None
                stage.locked_by = None
                stage.locked_at = None
                stage.last_validation = {}
            for key, value in payload.items():
                setattr(stage, key, value)
            stage.last_edited_by = request.user
            stage.last_edited_at = timezone.now()
            stage.save(
                update_fields=[
                    "title",
                    "description",
                    "purpose",
                    "entry_condition",
                    "acceptance_statement",
                    "exit_condition",
                    "key_deliverables",
                    "duration_estimate",
                    "risks_notes",
                    "last_edited_by",
                    "last_edited_at",
                    "status",
                    "proposed_by",
                    "proposed_at",
                    "locked_by",
                    "locked_at",
                    "last_validation",
                    "updated_at",
                ]
            )
            changed_blocks += 1

        if changed_blocks:
            messages.success(request, f"Saved {changed_blocks} block(s).")
        else:
            messages.info(request, "No changes to save.")

        if (request.POST.get("exit") or "").strip():
            return redirect("accounts:project_config_info", project_id=project.id)
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "stage_add":
        max_idx = ProjectPlanningStage.objects.filter(project=project).aggregate(Max("order_index")).get("order_index__max") or 0
        ProjectPlanningStage.objects.create(
            project=project,
            order_index=max_idx + 1,
            title=f"Stage {max_idx + 1}",
        )
        messages.success(request, "Stage added.")
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "revert_prepare":
        if not can_commit:
            messages.error(request, "Only the Project Committer can revert.")
            return redirect("projects:ppde_detail", project_id=project.id)
        target_id = (request.POST.get("target_wko_id") or "").strip()
        if not target_id.isdigit():
            messages.error(request, "Missing target version.")
            return redirect("projects:ppde_detail", project_id=project.id)
        target = ProjectWKO.objects.filter(project=project, id=int(target_id)).first()
        latest = ProjectWKO.objects.filter(project=project).order_by("-version").first()
        if not target:
            messages.error(request, "Target version not found.")
            return redirect("projects:ppde_detail", project_id=project.id)
        return render(
            request,
            "projects/ppde_revert_confirm.html",
            {
                "project": project,
                "target": target,
                "latest": latest,
            },
        )

    if request.method == "POST" and action == "revert_confirm":
        if not can_commit:
            messages.error(request, "Only the Project Committer can revert.")
            return redirect("projects:ppde_detail", project_id=project.id)
        target_id = (request.POST.get("target_wko_id") or "").strip()
        if not target_id.isdigit():
            messages.error(request, "Missing target version.")
            return redirect("projects:ppde_detail", project_id=project.id)
        target = ProjectWKO.objects.filter(project=project, id=int(target_id)).first()
        if not target:
            messages.error(request, "Target version not found.")
            return redirect("projects:ppde_detail", project_id=project.id)

        latest = ProjectWKO.objects.filter(project=project).aggregate(Max("version")).get("version__max") or 0
        ProjectWKO.objects.create(
            project=project,
            version=latest + 1,
            status=ProjectWKO.Status.DRAFT,
            structure_contract_key=(structure_contract.key if structure_contract else ""),
            structure_contract_version=(structure_contract.version if structure_contract else None),
            transform_contract_key=(transform_contract.key if transform_contract else ""),
            transform_contract_version=(transform_contract.version if transform_contract else None),
            seed_snapshot=seed_snapshot,
            content_json=target.content_json or {},
            change_summary=f"Revert fork from v{target.version}",
            created_by=request.user,
        )

        content = target.content_json or {}
        purpose_text = (content.get("planning_purpose") or "").strip()
        purpose.value_text = purpose_text
        purpose.status = ProjectPlanningPurpose.Status.DRAFT
        purpose.proposed_by = None
        purpose.proposed_at = None
        purpose.locked_by = None
        purpose.locked_at = None
        purpose.last_validation = {}
        purpose.last_edited_by = request.user
        purpose.last_edited_at = timezone.now()
        purpose.save(
            update_fields=[
                "value_text",
                "status",
                "proposed_by",
                "proposed_at",
                "locked_by",
                "locked_at",
                "last_validation",
                "last_edited_by",
                "last_edited_at",
                "updated_at",
            ]
        )

        ProjectPlanningStage.objects.filter(project=project).delete()
        stages_payload = content.get("stages") if isinstance(content, dict) else []
        title_map: Dict[str, ProjectPlanningStage] = {}
        if isinstance(stages_payload, list):
            for idx, row in enumerate(stages_payload, start=1):
                if not isinstance(row, dict):
                    continue
                stage_obj = ProjectPlanningStage.objects.create(
                    project=project,
                    order_index=int(row.get("order_index") or idx),
                    title=str(row.get("title") or ""),
                    description=str(row.get("description") or ""),
                    purpose=str(row.get("purpose") or ""),
                    entry_condition=str(row.get("entry_condition") or ""),
                    acceptance_statement=str(row.get("acceptance_statement") or ""),
                    exit_condition=str(row.get("exit_condition") or ""),
                    key_deliverables=list(row.get("key_deliverables") or []),
                    duration_estimate=str(row.get("duration_estimate") or ""),
                    risks_notes=str(row.get("risks_notes") or ""),
                    status=ProjectPlanningStage.Status.DRAFT,
                    proposed_by=None,
                    proposed_at=None,
                    locked_by=None,
                    locked_at=None,
                    last_validation={},
                    last_edited_by=request.user,
                    last_edited_at=timezone.now(),
                )
                if stage_obj.title:
                    title_map[stage_obj.title] = stage_obj

        ProjectPlanningMilestone.objects.filter(project=project).delete()
        ProjectPlanningAction.objects.filter(project=project).delete()
        ProjectPlanningRisk.objects.filter(project=project).delete()

        plan = content.get("plan") if isinstance(content, dict) else None
        if isinstance(plan, dict):
            for idx, m in enumerate(plan.get("milestones") or [], start=1):
                if not isinstance(m, dict):
                    continue
                st_title = str(m.get("stage_title") or "")
                ProjectPlanningMilestone.objects.create(
                    project=project,
                    stage=title_map.get(st_title),
                    order_index=idx,
                    title=str(m.get("title") or ""),
                    stage_title=st_title,
                    acceptance_statement=str(m.get("acceptance_statement") or ""),
                    target_date_hint=str(m.get("target_date_hint") or ""),
                    status=ProjectPlanningMilestone.Status.DRAFT,
                )
            for idx, a in enumerate(plan.get("actions") or [], start=1):
                if not isinstance(a, dict):
                    continue
                st_title = str(a.get("stage_title") or "")
                ProjectPlanningAction.objects.create(
                    project=project,
                    stage=title_map.get(st_title),
                    order_index=idx,
                    title=str(a.get("title") or ""),
                    stage_title=st_title,
                    owner_role=str(a.get("owner_role") or ""),
                    definition_of_done=str(a.get("definition_of_done") or ""),
                    effort_hint=str(a.get("effort_hint") or ""),
                    status=ProjectPlanningAction.Status.DRAFT,
                )
            for idx, r in enumerate(plan.get("risks") or [], start=1):
                if not isinstance(r, dict):
                    continue
                st_title = str(r.get("stage_title") or "")
                ProjectPlanningRisk.objects.create(
                    project=project,
                    stage=title_map.get(st_title),
                    order_index=idx,
                    title=str(r.get("title") or ""),
                    stage_title=st_title,
                    probability=str(r.get("probability") or ""),
                    impact=str(r.get("impact") or ""),
                    mitigation=str(r.get("mitigation") or ""),
                    status=ProjectPlanningRisk.Status.DRAFT,
                )

        messages.success(request, f"Reverted from v{target.version}; new draft v{latest + 1} created.")
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "stage_delete":
        if not can_commit:
            messages.error(request, "Only the Project Committer can delete stages.")
            return redirect("projects:ppde_detail", project_id=project.id)
        if not block_key.startswith("stage:"):
            messages.error(request, "Missing stage key.")
            return redirect("projects:ppde_detail", project_id=project.id)
        stage_id = block_key.split(":", 1)[1]
        stage = get_object_or_404(ProjectPlanningStage, id=stage_id, project=project)
        if ProjectPlanningStage.objects.filter(project=project).count() <= 1:
            messages.error(request, "At least one stage is required.")
            return redirect("projects:ppde_detail", project_id=project.id)
        stage.delete()
        messages.success(request, "Stage deleted.")
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "stage_duplicate":
        if not block_key.startswith("stage:"):
            messages.error(request, "Missing stage key.")
            return redirect("projects:ppde_detail", project_id=project.id)
        stage_id = block_key.split(":", 1)[1]
        stage = get_object_or_404(ProjectPlanningStage, id=stage_id, project=project)
        max_idx = ProjectPlanningStage.objects.filter(project=project).aggregate(Max("order_index")).get("order_index__max") or 0
        ProjectPlanningStage.objects.create(
            project=project,
            order_index=max_idx + 1,
            title=stage.title,
            description=stage.description,
            purpose=stage.purpose,
            entry_condition=stage.entry_condition,
            acceptance_statement=stage.acceptance_statement,
            exit_condition=stage.exit_condition,
            key_deliverables=list(stage.key_deliverables or []),
            duration_estimate=stage.duration_estimate,
            risks_notes=stage.risks_notes,
        )
        messages.success(request, "Stage duplicated.")
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action in ("stage_move_up", "stage_move_down"):
        if not block_key.startswith("stage:"):
            messages.error(request, "Missing stage key.")
            return redirect("projects:ppde_detail", project_id=project.id)
        stage_id = block_key.split(":", 1)[1]
        stage = get_object_or_404(ProjectPlanningStage, id=stage_id, project=project)
        stages_qs = list(ProjectPlanningStage.objects.filter(project=project).order_by("order_index", "id"))
        idx = next((i for i, s in enumerate(stages_qs) if s.id == stage.id), None)
        if idx is None:
            return redirect("projects:ppde_detail", project_id=project.id)
        swap_idx = idx - 1 if action == "stage_move_up" else idx + 1
        if swap_idx < 0 or swap_idx >= len(stages_qs):
            return redirect("projects:ppde_detail", project_id=project.id)
        other = stages_qs[swap_idx]
        stage.order_index, other.order_index = other.order_index, stage.order_index
        stage.save(update_fields=["order_index", "updated_at"])
        other.save(update_fields=["order_index", "updated_at"])
        return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + _block_anchor("stage:" + str(stage.id)))

    if request.method == "POST" and action in ("save_block", "verify_block", "propose_lock", "approve_lock", "reopen_block"):
        if not block_key:
            messages.error(request, "Missing block key.")
            return redirect("projects:ppde_detail", project_id=project.id)

        if block_key == "purpose":
            block = ProjectPlanningPurpose.objects.get(project=project)
            proposed_text = (request.POST.get("purpose_text") or "").strip()
            if not proposed_text:
                proposed_text = (block.value_text or "").strip()
            prior_text = (block.value_text or "").strip()
            changed = proposed_text != prior_text

            if block.status in (ProjectPlanningPurpose.Status.PROPOSED, ProjectPlanningPurpose.Status.PASS_LOCKED) and not can_commit:
                messages.error(request, "Only the Project Committer can edit this block.")
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

            if action == "verify_block":
                if not transform_contract:
                    messages.error(request, "No active contract configured. Contact administrator.")
                    return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)
                vobj = _ppde_validate_block(
                    block_key=block_key,
                    block_kind="purpose",
                    payload={"purpose_text": proposed_text},
                    seed_snapshot=seed_snapshot,
                    transform_contract=transform_contract,
                )
                block.last_validation = vobj
                block.save(update_fields=["last_validation", "updated_at"])
                request.session["ppde_last_validation_key"] = block_key
                request.session.modified = True
                messages.info(request, "Verification complete.")
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

            if action == "save_block":
                if not changed:
                    messages.info(request, "No changes.")
                    return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

                if can_commit and block.status in (ProjectPlanningPurpose.Status.PROPOSED, ProjectPlanningPurpose.Status.PASS_LOCKED):
                    block.status = ProjectPlanningPurpose.Status.DRAFT
                    block.proposed_by = None
                    block.proposed_at = None
                    block.locked_by = None
                    block.locked_at = None
                    block.last_validation = {}

                block.value_text = proposed_text
                block.last_edited_by = request.user
                block.last_edited_at = timezone.now()
                block.save(
                    update_fields=[
                        "value_text",
                        "last_edited_by",
                        "last_edited_at",
                        "status",
                        "proposed_by",
                        "proposed_at",
                        "locked_by",
                        "locked_at",
                        "last_validation",
                        "updated_at",
                    ]
                )
                messages.success(request, "Changes saved.")
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

            if action == "propose_lock":
                block.value_text = proposed_text
                block.last_edited_by = request.user
                block.last_edited_at = timezone.now()
                block.status = ProjectPlanningPurpose.Status.PROPOSED
                block.proposed_by = request.user
                block.proposed_at = timezone.now()
                block.save(
                    update_fields=[
                        "value_text",
                        "last_edited_by",
                        "last_edited_at",
                        "status",
                        "proposed_by",
                        "proposed_at",
                        "updated_at",
                    ]
                )
                messages.success(request, "Lock proposed.")
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

            if action == "approve_lock":
                if not can_commit:
                    messages.error(request, "Only the Project Committer can approve.")
                    return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)
                if block.status != ProjectPlanningPurpose.Status.PROPOSED:
                    messages.error(request, "Block is not proposed.")
                    return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)
                block.status = ProjectPlanningPurpose.Status.PASS_LOCKED
                block.locked_by = request.user
                block.locked_at = timezone.now()
                block.save(update_fields=["status", "locked_by", "locked_at", "updated_at"])
                messages.success(request, "Block locked.")
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

            if action == "reopen_block":
                if not can_commit:
                    messages.error(request, "Only the Project Committer can reopen.")
                    return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)
                block.status = ProjectPlanningPurpose.Status.DRAFT
                block.proposed_by = None
                block.proposed_at = None
                block.locked_by = None
                block.locked_at = None
                block.save(update_fields=["status", "proposed_by", "proposed_at", "locked_by", "locked_at", "updated_at"])
                messages.success(request, "Block reopened.")
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

        if block_key.startswith("stage:"):
            stage_id = block_key.split(":", 1)[1]
            stage = get_object_or_404(ProjectPlanningStage, id=stage_id, project=project)
            payload = _stage_payload_from_request(request)
            if not any(payload.values()):
                payload = _stage_payload_from_model(stage)
            changed = _stage_payload_changed(stage, payload)

            if stage.status in (ProjectPlanningStage.Status.PROPOSED, ProjectPlanningStage.Status.PASS_LOCKED) and not can_commit:
                messages.error(request, "Only the Project Committer can edit this block.")
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

            if action == "verify_block":
                if not transform_contract:
                    messages.error(request, "No active contract configured. Contact administrator.")
                    return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)
                vobj = _ppde_validate_block(
                    block_key=block_key,
                    block_kind="stage",
                    payload=payload,
                    seed_snapshot=seed_snapshot,
                    transform_contract=transform_contract,
                )
                stage.last_validation = vobj
                stage.save(update_fields=["last_validation", "updated_at"])
                request.session["ppde_last_validation_key"] = block_key
                request.session.modified = True
                messages.info(request, "Verification complete.")
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

            if action == "save_block":
                if not changed:
                    messages.info(request, "No changes.")
                    return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

                if can_commit and stage.status in (ProjectPlanningStage.Status.PROPOSED, ProjectPlanningStage.Status.PASS_LOCKED):
                    stage.status = ProjectPlanningStage.Status.DRAFT
                    stage.proposed_by = None
                    stage.proposed_at = None
                    stage.locked_by = None
                    stage.locked_at = None
                    stage.last_validation = {}

                for key, value in payload.items():
                    setattr(stage, key, value)
                stage.last_edited_by = request.user
                stage.last_edited_at = timezone.now()
                stage.save(
                    update_fields=[
                        "title",
                        "description",
                        "purpose",
                        "entry_condition",
                        "acceptance_statement",
                        "exit_condition",
                        "key_deliverables",
                        "duration_estimate",
                        "risks_notes",
                        "last_edited_by",
                        "last_edited_at",
                        "status",
                        "proposed_by",
                        "proposed_at",
                        "locked_by",
                        "locked_at",
                        "last_validation",
                        "updated_at",
                    ]
                )
                messages.success(request, "Changes saved.")
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

            if action == "propose_lock":
                for key, value in payload.items():
                    setattr(stage, key, value)
                stage.last_edited_by = request.user
                stage.last_edited_at = timezone.now()
                stage.status = ProjectPlanningStage.Status.PROPOSED
                stage.proposed_by = request.user
                stage.proposed_at = timezone.now()
                stage.save(
                    update_fields=[
                        "title",
                        "description",
                        "purpose",
                        "entry_condition",
                        "acceptance_statement",
                        "exit_condition",
                        "key_deliverables",
                        "duration_estimate",
                        "risks_notes",
                        "last_edited_by",
                        "last_edited_at",
                        "status",
                        "proposed_by",
                        "proposed_at",
                        "updated_at",
                    ]
                )
                messages.success(request, "Lock proposed.")
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

            if action == "approve_lock":
                if not can_commit:
                    messages.error(request, "Only the Project Committer can approve.")
                    return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)
                if stage.status != ProjectPlanningStage.Status.PROPOSED:
                    messages.error(request, "Block is not proposed.")
                    return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)
                stage.status = ProjectPlanningStage.Status.PASS_LOCKED
                stage.locked_by = request.user
                stage.locked_at = timezone.now()
                stage.save(update_fields=["status", "locked_by", "locked_at", "updated_at"])
                messages.success(request, "Block locked.")
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

            if action == "reopen_block":
                if not can_commit:
                    messages.error(request, "Only the Project Committer can reopen.")
                    return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)
                stage.status = ProjectPlanningStage.Status.DRAFT
                stage.proposed_by = None
                stage.proposed_at = None
                stage.locked_by = None
                stage.locked_at = None
                stage.save(update_fields=["status", "proposed_by", "proposed_at", "locked_by", "locked_at", "updated_at"])
                messages.success(request, "Block reopened.")
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

    if request.method == "POST" and action == "commit_wko_version":
        if not can_commit:
            messages.error(request, "Only the Project Committer can commit.")
            return redirect("projects:ppde_detail", project_id=project.id)

        purpose, stages = _ensure_ppde_blocks(project)
        non_locked = 0
        if purpose.status != ProjectPlanningPurpose.Status.PASS_LOCKED:
            non_locked += 1
        non_locked += ProjectPlanningStage.objects.filter(
            project=project,
        ).exclude(status=ProjectPlanningStage.Status.PASS_LOCKED).count()
        if non_locked:
            messages.warning(request, "Some blocks are not locked; committing anyway.")

        stage_titles = [s.title.strip() for s in stages if (s.title or "").strip()]
        if not stages:
            messages.warning(request, "No stages present.")
        for stage in stages:
            if not (stage.title or "").strip():
                messages.warning(request, f"Stage {stage.order_index} missing title.")
            if not (stage.acceptance_statement or "").strip():
                messages.warning(request, f"Stage {stage.order_index} missing acceptance statement.")

        milestones_qs = ProjectPlanningMilestone.objects.filter(project=project).order_by("order_index", "id")
        actions_qs = ProjectPlanningAction.objects.filter(project=project).order_by("order_index", "id")
        risks_qs = ProjectPlanningRisk.objects.filter(project=project).order_by("order_index", "id")

        if not milestones_qs.exists():
            messages.warning(request, "Plan milestones are empty.")
        if actions_qs.count() < 5:
            messages.warning(request, "Plan has fewer than 5 actions.")
        if not risks_qs.exists():
            messages.warning(request, "Plan risks are empty.")

        for m in milestones_qs:
            if not (m.acceptance_statement or "").strip():
                messages.warning(request, "Milestone missing acceptance statement: " + (m.title or ""))
            if (m.stage_title or "").strip() and m.stage_title.strip() not in stage_titles:
                messages.warning(request, "Milestone stage title not found: " + m.stage_title)
        for a in actions_qs:
            if not (a.definition_of_done or "").strip():
                messages.warning(request, "Action missing definition of done: " + (a.title or ""))
            if (a.stage_title or "").strip() and a.stage_title.strip() not in stage_titles:
                messages.warning(request, "Action stage title not found: " + a.stage_title)
        for r in risks_qs:
            if not (r.mitigation or "").strip():
                messages.warning(request, "Risk missing mitigation: " + (r.title or ""))
            if (r.stage_title or "").strip() and r.stage_title.strip() not in stage_titles:
                messages.warning(request, "Risk stage title not found: " + r.stage_title)

        payload = {
            "planning_purpose": (purpose.value_text or "").strip(),
            "stages": [],
            "plan": {
                "milestones": [],
                "actions": [],
                "risks": [],
                "assumptions": [],
                "dependencies": [],
            },
        }
        for stage in ProjectPlanningStage.objects.filter(project=project).order_by("order_index", "id"):
            payload["stages"].append(
                {
                    "id": stage.id,
                    "order_index": stage.order_index,
                    "title": (stage.title or "").strip(),
                    "description": (stage.description or "").strip(),
                    "purpose": (stage.purpose or "").strip(),
                    "entry_condition": (stage.entry_condition or "").strip(),
                    "acceptance_statement": (stage.acceptance_statement or "").strip(),
                    "exit_condition": (stage.exit_condition or "").strip(),
                    "key_deliverables": list(stage.key_deliverables or []),
                    "duration_estimate": (stage.duration_estimate or "").strip(),
                    "risks_notes": (stage.risks_notes or "").strip(),
                }
            )

        for m in milestones_qs:
            payload["plan"]["milestones"].append(
                {
                    "title": m.title,
                    "stage_title": m.stage_title,
                    "acceptance_statement": m.acceptance_statement,
                    "target_date_hint": m.target_date_hint,
                }
            )
        for a in actions_qs:
            payload["plan"]["actions"].append(
                {
                    "title": a.title,
                    "stage_title": a.stage_title,
                    "owner_role": a.owner_role,
                    "definition_of_done": a.definition_of_done,
                    "effort_hint": a.effort_hint,
                }
            )
        for r in risks_qs:
            payload["plan"]["risks"].append(
                {
                    "title": r.title,
                    "stage_title": r.stage_title,
                    "probability": r.probability,
                    "impact": r.impact,
                    "mitigation": r.mitigation,
                }
            )

        latest = ProjectWKO.objects.filter(project=project).aggregate(Max("version")).get("version__max") or 0
        ProjectWKO.objects.create(
            project=project,
            version=latest + 1,
            status=ProjectWKO.Status.DRAFT,
            structure_contract_key=(structure_contract.key if structure_contract else ""),
            structure_contract_version=(structure_contract.version if structure_contract else None),
            transform_contract_key=(transform_contract.key if transform_contract else ""),
            transform_contract_version=(transform_contract.version if transform_contract else None),
            seed_snapshot=seed_snapshot,
            content_json=payload,
            change_summary="Committed from PPDE working draft",
            created_by=request.user,
        )
        messages.success(request, "WKO version created.")
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "promote_plan_to_tasks":
        if not can_commit:
            messages.error(request, "Only the Project Committer can promote tasks.")
            return redirect("projects:ppde_detail", project_id=project.id)
        latest_wko = (
            ProjectWKO.objects
            .filter(project=project)
            .order_by("-version")
            .first()
        )
        if not latest_wko or not isinstance(latest_wko.content_json, dict):
            messages.warning(request, "No Plan WKO available.")
            return redirect("projects:ppde_detail", project_id=project.id)
        plan = latest_wko.content_json.get("plan") if isinstance(latest_wko.content_json, dict) else None
        actions = plan.get("actions") if isinstance(plan, dict) else None
        if not actions:
            messages.warning(request, "No plan actions to promote.")
            return redirect("projects:ppde_detail", project_id=project.id)

        existing = ProjectExecutionTask.objects.filter(
            project=project,
            source_wko_version=latest_wko.version,
        )
        existing_keys = set((t.title, t.stage_title) for t in existing)
        if existing_keys:
            messages.warning(request, "Tasks already exist for this plan version; duplicates will be skipped.")

        created = 0
        for item in actions:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            stage_title = str(item.get("stage_title") or "").strip()
            if not title:
                continue
            if (title, stage_title) in existing_keys:
                continue
            desc_parts = []
            owner_role = str(item.get("owner_role") or "").strip()
            dod = str(item.get("definition_of_done") or "").strip()
            effort = str(item.get("effort_hint") or "").strip()
            if owner_role:
                desc_parts.append("Owner role: " + owner_role)
            if dod:
                desc_parts.append("DoD: " + dod)
            if effort:
                desc_parts.append("Effort: " + effort)
            description = "\n".join(desc_parts)
            ProjectExecutionTask.objects.create(
                project=project,
                title=title,
                description=description,
                stage_title=stage_title,
                source_wko_version=latest_wko.version,
            )
            created += 1
        messages.success(request, f"{created} tasks created from Plan v{latest_wko.version}.")
        return redirect("projects:ppde_detail", project_id=project.id)

    purpose, stages = _ensure_ppde_blocks(project)
    seed_view = (request.session.get("ppde_seed_view") or "full").strip().lower()
    seed_source = seed_snapshot
    if seed_view == "short" and isinstance(project.ppde_seed_summary, dict) and project.ppde_seed_summary:
        seed_source = project.ppde_seed_summary

    seed_context = []
    for k in seed_snapshot.keys():
        v = (seed_source.get(k) or "").strip()
        if not v:
            continue
        seed_context.append({"key": k, "value": v})

    show_validation_key = (request.session.get("ppde_last_validation_key") or "").strip()
    if show_validation_key:
        request.session.pop("ppde_last_validation_key", None)
        request.session.modified = True

    ppde_help_log = _get_ppde_help_log(request, project.id)
    auto_key = "ppde_help_auto_open_" + str(project.id)
    ppde_help_auto_open = bool(request.session.get(auto_key))
    if ppde_help_auto_open:
        request.session.pop(auto_key, None)
        request.session.modified = True

    stage_specs: List[Dict[str, Any]] = []
    for stage in stages:
        stage_specs.append(
            {
                "id": stage.id,
                "order_index": stage.order_index,
                "title": stage.title,
                "description": stage.description,
                "purpose": stage.purpose,
                "entry_condition": stage.entry_condition,
                "acceptance_statement": stage.acceptance_statement,
                "exit_condition": stage.exit_condition,
                "key_deliverables": "\n".join(stage.key_deliverables or []),
                "duration_estimate": stage.duration_estimate,
                "risks_notes": stage.risks_notes,
                "status": stage.status,
                "proposed_by": (getattr(stage.proposed_by, "username", "") or ""),
                "locked_by": (getattr(stage.locked_by, "username", "") or ""),
                "last_validation": stage.last_validation or {},
                "validation_key": "stage:" + str(stage.id),
            }
        )

    wko_versions = (
        ProjectWKO.objects
        .filter(project=project)
        .order_by("-version")
    )
    latest_wko = wko_versions.first()

    all_locked = (
        purpose.status == ProjectPlanningPurpose.Status.PASS_LOCKED
        and all(s["status"] == ProjectPlanningStage.Status.PASS_LOCKED for s in stage_specs)
    )
    has_proposed = (
        purpose.status == ProjectPlanningPurpose.Status.PROPOSED
        or any(s["status"] == ProjectPlanningStage.Status.PROPOSED for s in stage_specs)
    )

    ppde_status_badge = {"text": "Draft", "class": "bg-secondary"}
    if has_proposed:
        ppde_status_badge = {"text": "Proposed", "class": "bg-warning text-dark"}
    elif all_locked:
        ppde_status_badge = {"text": "Ready to Commit", "class": "bg-success"}

    milestones = list(ProjectPlanningMilestone.objects.filter(project=project).order_by("order_index", "id"))
    actions = list(ProjectPlanningAction.objects.filter(project=project).order_by("order_index", "id"))
    risks = list(ProjectPlanningRisk.objects.filter(project=project).order_by("order_index", "id"))
    plan_preview = request.session.get(_ppde_plan_preview_key(project.id))
    stage_preview = request.session.get(_ppde_stage_preview_key(project.id))
    stage_edit_log = _get_ppde_stage_edit_log(request, project.id)
    stage_edit_auto_open = bool(request.session.get("ppde_stage_edit_auto_open_" + str(project.id)))

    context = {
        "project": project,
        "ppde_can_commit": can_commit,
        "ppde_progress": {
            "total": 1 + len(stage_specs),
            "locked": (1 if purpose.status == ProjectPlanningPurpose.Status.PASS_LOCKED else 0)
            + sum(1 for s in stage_specs if s["status"] == ProjectPlanningStage.Status.PASS_LOCKED),
            "proposed": (1 if purpose.status == ProjectPlanningPurpose.Status.PROPOSED else 0)
            + sum(1 for s in stage_specs if s["status"] == ProjectPlanningStage.Status.PROPOSED),
            "draft": (1 if purpose.status == ProjectPlanningPurpose.Status.DRAFT else 0)
            + sum(1 for s in stage_specs if s["status"] == ProjectPlanningStage.Status.DRAFT),
            "all_locked": all_locked,
            "has_proposed": has_proposed,
        },
        "ppde_status_badge": ppde_status_badge,
        "structure_contract": structure_contract,
        "transform_contract": transform_contract,
        "plan_contract": plan_contract,
        "wko_versions": wko_versions,
        "latest_wko": latest_wko,
        "seed_context": seed_context,
        "seed_view": seed_view,
        "seed_has_summary": bool(isinstance(project.ppde_seed_summary, dict) and project.ppde_seed_summary),
        "ui_return_to": reverse("accounts:project_config_info", kwargs={"project_id": project.id}),
        "ppde_help_log": ppde_help_log,
        "ppde_help_auto_open": ppde_help_auto_open,
        "purpose": {
            "value_text": purpose.value_text,
            "status": purpose.status,
            "proposed_by": (getattr(purpose.proposed_by, "username", "") or ""),
            "locked_by": (getattr(purpose.locked_by, "username", "") or ""),
            "last_validation": purpose.last_validation or {},
        },
        "stages": stage_specs,
        "show_validation_key": show_validation_key,
        "plan_milestones": milestones,
        "plan_actions": actions,
        "plan_risks": risks,
        "plan_preview": plan_preview,
        "stage_preview": stage_preview,
        "stage_edit_log": stage_edit_log,
        "stage_edit_auto_open": stage_edit_auto_open,
    }

    return render(request, "projects/ppde_detail.html", context)
