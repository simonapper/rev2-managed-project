# projects/views_ppde_ui.py
# PPDE (Planning PDO editor) UI

from __future__ import annotations

import json
from typing import Any, Dict, List

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from urllib.parse import urlencode
from django.utils import timezone
from django.views.decorators.http import require_POST

from chats.services.llm import generate_panes
from chats.services.turns import build_chat_turn_context
from chats.models import ChatWorkspace
from projects.models import (
    PhaseContract,
    Project,
    ProjectCKO,
    ProjectPlanningPurpose,
    ProjectPlanningStage,
    ProjectTopicChat,
    ProjectPDO,
)
from projects.services_project_membership import can_edit_ppde, is_project_committer
from projects.services_topic_chat import get_or_create_topic_chat


WORKING_METHOD_BLOCK = (
    "Working method\n"
    "Treat the listed sources as authoritative in this order: CKO -> Planning Purpose -> current draft -> other artefacts.\n"
    "Do not invent project facts not present in the sources; ask if missing.\n"
    "Work iteratively:\n"
    "- Identify gaps or ambiguities.\n"
    "- Ask concise clarification questions if needed.\n"
    "- Propose a revised draft.\n"
    "- Confirm with the user before finalising output.\n"
    "Keep all suggestions consistent with the project's stated goals, constraints, and acceptance criteria.\n"
    "\n"
    "Output discipline\n"
    "When the user indicates readiness, return only the required JSON structure.\n"
    "Do not include explanations, markdown, or extra text in the final output.\n"
)

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
    "\n"
    + WORKING_METHOD_BLOCK
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
    "- It must be plain language and planning-oriented.\n"
    "\n"
    + WORKING_METHOD_BLOCK
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
    '      "purpose": "string",\n'
    '      "inputs": "string",\n'
    '      "stage_process": "string",\n'
    '      "outputs": "string",\n'
    '      "assumptions": "string",\n'
    '      "duration_estimate": "string",\n'
    '      "risks_notes": "string"\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "\n"
    "Rules:\n"
    "- stages length: 3 to 8 is preferred; you may exceed only if necessary.\n"
    "- Each stage must be concrete and non-overlapping.\n"
    "- inputs describe material known at the start.\n"
    "- stage_process is 1-3 sentences describing how inputs become outputs.\n"
    "- outputs are one per line; these feed the next stage.\n"
    "- assumptions are optional; include only if material.\n"
    "- duration_estimate is free text and may include deadlines.\n"
    "\n"
    + WORKING_METHOD_BLOCK
)


def _ppde_help_key(project_id: int) -> str:
    return "ppde_help_log_" + str(project_id)


def _ppde_stage_preview_key(project_id: int) -> str:
    return "ppde_stage_preview_" + str(project_id)


def _ppde_stage_edit_key(project_id: int) -> str:
    return "ppde_stage_edit_log_" + str(project_id)


def _get_ppde_help_log(request, project_id: int) -> List[Dict[str, str]]:
    return list(request.session.get(_ppde_help_key(project_id)) or [])


def _get_ppde_stage_edit_log(request, project_id: int) -> List[Dict[str, str]]:
    return list(request.session.get(_ppde_stage_edit_key(project_id)) or [])

def _ckos_to_bullets(seed_snapshot: Dict[str, Any], keywords: List[str], limit: int = 6) -> str:
    rows = []
    for k in sorted(seed_snapshot.keys()):
        v = (seed_snapshot.get(k) or "").strip()
        if not v:
            continue
        key_l = k.lower()
        if any(word in key_l for word in keywords):
            rows.append("- " + k + ": " + v)
        if len(rows) >= limit:
            break
    if rows:
        return "From CKO:\n" + "\n".join(rows)

    # Fallback: first few seed items.
    for k in sorted(seed_snapshot.keys()):
        v = (seed_snapshot.get(k) or "").strip()
        if not v:
            continue
        rows.append("- " + k + ": " + v)
        if len(rows) >= min(limit, 3):
            break
    return "From CKO:\n" + "\n".join(rows) if rows else ""

@require_POST
@login_required
def ppde_topic_chat_open(request, project_id: int) -> HttpResponse:
    project = get_object_or_404(Project, pk=project_id)
    if not can_edit_ppde(project, request.user):
        messages.error(request, "You do not have permission to open a topic chat for this section.")
        return redirect("projects:ppde_detail", project_id=project.id)

    structure_contract = PhaseContract.objects.filter(key="STRUCTURE_PROJECT", is_active=True).first()
    transform_contract = PhaseContract.objects.filter(key="TRANSFORM_STAGE", is_active=True).first()
    topic_type = (request.POST.get("topic_type") or "").strip().upper()
    if topic_type == "EXEC_PLAN":
        stage_id_raw = (request.POST.get("stage_id") or "").strip()
        anchor = "#ppde-stage-" + stage_id_raw if stage_id_raw.isdigit() else ""
        messages.info(request, "Execution planning is produced in MDE, not PPDE.")
        return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)
    if topic_type not in ("PURPOSE", "STAGE"):
        messages.error(request, "Invalid topic chat request.")
        return redirect("projects:ppde_detail", project_id=project.id)
    open_in = (request.POST.get("open_in") or "").strip().lower()

    if topic_type == "PURPOSE":
        purpose = ProjectPlanningPurpose.objects.filter(project=project).first()
        purpose_text = (purpose.value_text or "") if purpose else ""
        seed_snapshot = _seed_snapshot_from_cko(project)
        seed_lines = []
        for k in sorted(seed_snapshot.keys()):
            v = (seed_snapshot.get(k) or "").strip()
            if v:
                seed_lines.append("- " + k + ": " + v)
        seed_text = "CKO snapshot:\n" + ("\n".join(seed_lines) if seed_lines else "(none)")
        contract_text = _contract_text(structure_contract)

        can_commit = is_project_committer(project, request.user)
        purpose_status = (purpose.status if purpose else ProjectPlanningPurpose.Status.DRAFT)
        if purpose_status != ProjectPlanningPurpose.Status.DRAFT and not can_commit:
            messages.error(request, "You do not have permission to open a topic chat for this section.")
            return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + "#ppde-purpose")

        seed_user_text = "\n".join(
            [
                "Topic chat: PPDE Purpose",
                "Scope: PPDE",
                "Topic key: PURPOSE",
                "Label: Planning Purpose",
                "",
                "Current Planning Purpose:",
                purpose_text or "(empty)",
                "",
                "Contract:",
                contract_text or "(none)",
                "",
                "Required JSON schema:",
                "{\"planning_purpose\": \"<3-6 sentences>\"}",
                "",
                seed_text,
                "",
                "Sources order: CKO -> planning_purpose -> current draft -> user clarifications.",
                "Do not invent project facts; ask if missing.",
                "When finalising: JSON only, no markdown, no commentary.",
                "",
                "Goal: Help produce a Planning Purpose (3-6 sentences).",
                "Output: JSON only: {\"planning_purpose\": \"...\"}.",
                "Success criteria: ready to paste into the Planning Purpose section.",
                "",
                WORKING_METHOD_BLOCK,
            ]
        )

        chat = get_or_create_topic_chat(
            project=project,
            user=request.user,
            scope="PPDE",
            topic_key="PURPOSE",
            title="PPDE-" + (project.name or "") + "-Purpose-" + request.user.username,
            seed_user_text=seed_user_text,
            mode="CONTROLLED",
        )

        request.session["rw_active_project_id"] = project.id
        request.session["rw_active_chat_id"] = chat.id
        request.session.modified = True

        if open_in == "drawer":
            base = reverse("projects:ppde_detail", kwargs={"project_id": project.id})
            qs = urlencode({"ppde_chat_id": str(chat.id), "ppde_chat_open": "1"})
            return redirect(base + "?" + qs + "#ppde-purpose")
        return redirect(reverse("accounts:chat_detail", args=[chat.id]))

    stage_id_raw = (request.POST.get("stage_id") or "").strip()
    try:
        stage_id = int(stage_id_raw)
    except Exception:
        messages.error(request, "Invalid stage.")
        return redirect("projects:ppde_detail", project_id=project.id)

    stage = ProjectPlanningStage.objects.filter(project=project, id=stage_id).first()
    if not stage:
        messages.error(request, "Stage not found.")
        return redirect("projects:ppde_detail", project_id=project.id)

    can_commit = is_project_committer(project, request.user)
    if stage.status != ProjectPlanningStage.Status.DRAFT and not can_commit:
        messages.error(request, "You do not have permission to open a topic chat for this section.")
        return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + "#ppde-stage-" + str(stage.id))

    purpose = ProjectPlanningPurpose.objects.filter(project=project).first()
    purpose_text = (purpose.value_text or "") if purpose else ""
    seed_snapshot = _seed_snapshot_from_cko(project)
    seed_lines = []
    for k in sorted(seed_snapshot.keys()):
        v = (seed_snapshot.get(k) or "").strip()
        if v:
            seed_lines.append("- " + k + ": " + v)
    seed_text = "CKO snapshot:\n" + ("\n".join(seed_lines) if seed_lines else "(none)")
    contract_text = _contract_text(transform_contract)

    stage_payload = {
        "title": (stage.title or "").strip(),
        "purpose": (stage.purpose or "").strip(),
        "inputs": (stage.inputs or "").strip(),
        "stage_process": (stage.stage_process or "").strip(),
        "outputs": (stage.outputs or "").strip(),
        "assumptions": (stage.assumptions or "").strip(),
        "duration_estimate": (stage.duration_estimate or "").strip(),
        "risks_notes": (stage.risks_notes or "").strip(),
    }

    stage_title = (stage.title or "").strip() or ("Stage " + str(stage.order_index))
    label = f"Stage {stage.order_index}: {stage_title}"

    seed_user_text = "\n".join(
        [
            "Topic chat: PPDE Stage",
            "Scope: PPDE",
            "Topic key: STAGE:" + str(stage.id),
            "Label: " + label,
            "",
            "Current Planning Purpose:",
            purpose_text or "(empty)",
            "",
            "Contract:",
            contract_text or "(none)",
            "",
            "Required JSON schema:",
            "{",
            "  \"title\": \"\",",
            "  \"purpose\": \"\",",
            "  \"inputs\": \"\",",
            "  \"stage_process\": \"\",",
            "  \"outputs\": \"\",",
            "  \"assumptions\": \"\",",
            "  \"duration_estimate\": \"\",",
            "  \"risks_notes\": \"\"",
            "}",
            "",
            seed_text,
            "",
            "Current Stage Draft (JSON):",
            json.dumps(stage_payload, indent=2, ensure_ascii=True),
            "",
            "Sources order: CKO -> planning_purpose -> current stage -> user clarifications.",
            "Do not invent project facts; ask if missing.",
            "When finalising: JSON only, no markdown, no commentary.",
            "",
            "Goal: Help produce a revised Stage draft matching the stage schema.",
            "Output: JSON only matching stage schema.",
            "Success criteria: ready to paste into the PPDE stage section.",
            "",
            WORKING_METHOD_BLOCK,
        ]
    )

    chat = get_or_create_topic_chat(
        project=project,
        user=request.user,
        scope="PPDE",
        topic_key="STAGE:" + str(stage.id),
        title="PPDE-" + (project.name or "") + "-" + stage_title + "-" + request.user.username,
        seed_user_text=seed_user_text,
        mode="CONTROLLED",
    )

    request.session["rw_active_project_id"] = project.id
    request.session["rw_active_chat_id"] = chat.id
    request.session.modified = True

    if open_in == "drawer":
        base = reverse("projects:ppde_detail", kwargs={"project_id": project.id})
        qs = urlencode({"ppde_chat_id": str(chat.id), "ppde_chat_open": "1"})
        return redirect(base + "?" + qs + "#ppde-stage-" + str(stage.id))
    return redirect(reverse("accounts:chat_detail", args=[chat.id]))


def _ppde_help_answer(*, question: str, project: Project, user) -> str:
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
        user=user,
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


def _extract_json_object(text: str) -> Dict[str, Any] | None:
    s = (text or "").strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    try:
        decoder = json.JSONDecoder()
    except Exception:
        return None
    idx = s.find("{")
    while idx != -1:
        try:
            obj, _end = decoder.raw_decode(s[idx:])
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
        idx = s.find("{", idx + 1)
    return None


def _parse_planning_purpose_json(raw_output: str) -> tuple[str | None, str | None]:
    raw_output = (raw_output or "").strip()
    data = _extract_json_object(raw_output)
    if data is None:
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
    user = None,
) -> Dict[str, Any]:
    seed_lines = []
    for k in sorted(seed_snapshot.keys()):
        v = (seed_snapshot.get(k) or "").strip()
        if not v:
            continue
        seed_lines.append("- " + k + ": " + v)
    seed_text = "Seeded CKO context:\n" + ("\n".join(seed_lines) if seed_lines else "(none)")

    if block_kind == "purpose":
        block_text = (
            "Planning purpose:\n" + (payload.get("purpose_text") or "") + "\n"
            "PDO summary:\n" + (payload.get("pdo_summary") or "") + "\n"
            "Planning constraints:\n" + (payload.get("planning_constraints") or "") + "\n"
            "Assumptions:\n" + (payload.get("assumptions") or "") + "\n"
            "CKO alignment (stage 1 inputs match):\n" + (payload.get("cko_alignment_stage1_inputs_match") or "") + "\n"
            "CKO alignment (final outputs match):\n" + (payload.get("cko_alignment_final_outputs_match") or "")
        )
    else:
        block_text = (
            "Stage fields:\n"
            + "title: " + (payload.get("title") or "") + "\n"
            + "purpose: " + (payload.get("purpose") or "") + "\n"
            + "inputs: " + (payload.get("inputs") or "") + "\n"
            + "stage_process: " + (payload.get("stage_process") or "") + "\n"
            + "outputs: " + (payload.get("outputs") or "") + "\n"
            + "assumptions: " + (payload.get("assumptions") or "") + "\n"
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
        user=user,
    )
    raw = str(panes.get("output") or "")
    out = _parse_validation_json(raw_output=raw, block_key=block_key)
    out["debug_user_text"] = user_text
    out["debug_system_blocks"] = [PPDE_VALIDATOR_BOILERPLATE]
    return out


def _parse_stage_map_json(raw_output: str) -> tuple[List[Dict[str, Any]], str | None]:
    raw_output = (raw_output or "").strip()
    data = _extract_json_object(raw_output)
    if data is None:
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
        outputs_val = item.get("outputs")
        if outputs_val is None:
            outputs_val = item.get("key_deliverables")
        if isinstance(outputs_val, list):
            outputs_lines = [str(x).strip() for x in outputs_val if str(x).strip()]
            outputs_text = "\n".join(outputs_lines)
        else:
            outputs_text = str(outputs_val or "").strip()
        if not outputs_text:
            outputs_text = "TBD"
        inputs_val = item.get("inputs")
        if inputs_val is None:
            inputs_val = item.get("entry_conditions", item.get("entry_condition", ""))
        inputs_text = str(inputs_val or "").strip()
        stage_process_text = str(item.get("stage_process") or item.get("description") or "").strip()
        assumptions_text = str(item.get("assumptions") or item.get("key_variables") or "").strip()
        out.append(
            {
                "title": str(item.get("title") or "").strip(),
                "purpose": str(item.get("purpose") or "").strip(),
                "inputs": inputs_text,
                "stage_process": stage_process_text,
                "outputs": outputs_text,
                "assumptions": assumptions_text,
                "duration_estimate": str(item.get("duration_estimate") or "").strip(),
                "risks_notes": str(item.get("risks_notes") or "").strip(),
            }
        )
    if not out:
        return [], "No valid stages returned."
    return out, None


def _summarize_stages(stages_out: List[Dict[str, Any]]) -> Dict[str, Any]:
    items = []
    for stage in stages_out[:8]:
        title = (stage.get("title") or "").strip()
        desc = (stage.get("purpose") or "").strip()
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
        inputs = stage_data.get("inputs", stage_data.get("entry_conditions", stage_data.get("entry_condition", "")))
        stage_process = stage_data.get("stage_process", stage_data.get("description", ""))
        outputs_val = stage_data.get("outputs", stage_data.get("key_deliverables", ""))
        if isinstance(outputs_val, list):
            outputs_val = "\n".join([str(x).strip() for x in outputs_val if str(x).strip()])
        else:
            outputs_val = str(outputs_val or "")
        ProjectPlanningStage.objects.create(
            project=project,
            order_index=idx,
            title=stage_data.get("title", ""),
            purpose=stage_data.get("purpose", ""),
            inputs=str(inputs or ""),
            stage_process=str(stage_process or ""),
            outputs=str(outputs_val or ""),
            assumptions=str(stage_data.get("assumptions", stage_data.get("key_variables", "")) or ""),
            duration_estimate=stage_data.get("duration_estimate", ""),
            risks_notes=stage_data.get("risks_notes", ""),
            status=ProjectPlanningStage.Status.DRAFT,
            proposed_by=None,
            proposed_at=None,
            locked_by=None,
            locked_at=None,
            last_validation={},
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
    return {
        "title": (request.POST.get("title") or "").strip(),
        "purpose": (request.POST.get("purpose") or "").strip(),
        "inputs": (request.POST.get("inputs") or "").strip(),
        "stage_process": (request.POST.get("stage_process") or "").strip(),
        "outputs": (request.POST.get("outputs") or "").strip(),
        "assumptions": (request.POST.get("assumptions") or "").strip(),
        "duration_estimate": (request.POST.get("duration_estimate") or "").strip(),
        "risks_notes": (request.POST.get("risks_notes") or "").strip(),
    }


def _stage_payload_from_post(request, stage_id: int) -> Dict[str, Any]:
    prefix = "stage_" + str(stage_id) + "__"
    return {
        "title": (request.POST.get(prefix + "title") or "").strip(),
        "purpose": (request.POST.get(prefix + "purpose") or "").strip(),
        "inputs": (request.POST.get(prefix + "inputs") or "").strip(),
        "stage_process": (request.POST.get(prefix + "stage_process") or "").strip(),
        "outputs": (request.POST.get(prefix + "outputs") or "").strip(),
        "assumptions": (request.POST.get(prefix + "assumptions") or "").strip(),
        "duration_estimate": (request.POST.get(prefix + "duration_estimate") or "").strip(),
        "risks_notes": (request.POST.get(prefix + "risks_notes") or "").strip(),
    }


def _stage_payload_from_model(stage: ProjectPlanningStage) -> Dict[str, Any]:
    return {
        "title": (stage.title or "").strip(),
        "purpose": (stage.purpose or "").strip(),
        "inputs": (stage.inputs or "").strip(),
        "stage_process": (stage.stage_process or "").strip(),
        "outputs": (stage.outputs or "").strip(),
        "assumptions": (stage.assumptions or "").strip(),
        "duration_estimate": (stage.duration_estimate or "").strip(),
        "risks_notes": (stage.risks_notes or "").strip(),
    }


def _stage_payload_changed(stage: ProjectPlanningStage, payload: Dict[str, Any]) -> bool:
    prior = _stage_payload_from_model(stage)
    for key in prior:
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
    wants_json = (request.headers.get("X-Requested-With") or "").lower() == "xmlhttprequest"
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

    if request.method == "GET":
        # Auto-prefill PDO fields from CKO if empty; auto-unlock if fields are blank.
        needs_unlock = False
        updates = {}

        if not (purpose.pdo_summary or "").strip():
            summary = (purpose.value_text or "").strip()
            if summary:
                parts = summary.split(".")
                updates["pdo_summary"] = (parts[0] + "." + (parts[1] + "." if len(parts) > 1 else "")).strip()
            else:
                updates["pdo_summary"] = _ckos_to_bullets(seed_snapshot, ["summary", "goal", "objective", "purpose"])

        if not (purpose.planning_constraints or "").strip():
            updates["planning_constraints"] = _ckos_to_bullets(
                seed_snapshot,
                ["constraint", "limit", "policy", "compliance", "guard", "rule", "risk"],
            )

        if not (purpose.assumptions or "").strip():
            updates["assumptions"] = _ckos_to_bullets(
                seed_snapshot,
                ["assumption", "assume", "dependency", "context", "environment", "audience"],
            )

        if not (purpose.cko_alignment_stage1_inputs_match or "").strip():
            updates["cko_alignment_stage1_inputs_match"] = _ckos_to_bullets(
                seed_snapshot,
                ["input", "context", "scope", "resource", "constraint"],
            )

        if not (purpose.cko_alignment_final_outputs_match or "").strip():
            updates["cko_alignment_final_outputs_match"] = _ckos_to_bullets(
                seed_snapshot,
                ["output", "deliverable", "goal", "success", "result"],
            )

        if any(not (getattr(purpose, k) or "").strip() for k in [
            "pdo_summary",
            "planning_constraints",
            "assumptions",
            "cko_alignment_stage1_inputs_match",
            "cko_alignment_final_outputs_match",
        ]):
            needs_unlock = True

        if updates:
            for k, v in updates.items():
                if v and not (getattr(purpose, k) or "").strip():
                    setattr(purpose, k, v)

        if needs_unlock and purpose.status == ProjectPlanningPurpose.Status.PASS_LOCKED:
            purpose.status = ProjectPlanningPurpose.Status.DRAFT
            purpose.proposed_by = None
            purpose.proposed_at = None
            purpose.locked_by = None
            purpose.locked_at = None
            purpose.last_validation = {}

        if updates or needs_unlock:
            purpose.last_edited_by = request.user
            purpose.last_edited_at = timezone.now()
            purpose.save(
                update_fields=[
                    "pdo_summary",
                    "planning_constraints",
                    "assumptions",
                    "cko_alignment_stage1_inputs_match",
                    "cko_alignment_final_outputs_match",
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
        answer = _ppde_help_answer(question=question, project=project, user=request.user)
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
            user=request.user,
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

    if request.method == "POST" and action in {
        "generate_plan_from_stages",
        "derive_stage_plan",
        "approve_stage_plan",
        "promote_plan_to_tasks",
    }:
        block_key = (request.POST.get("block_key") or "").strip()
        anchor = ""
        if block_key.startswith("stage:"):
            anchor = "#ppde-stage-" + block_key.split(":", 1)[1]
        messages.info(request, "Execution planning is produced in MDE, not PPDE.")
        return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

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
            user=request.user,
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
            user=request.user,
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
            user=request.user,
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

    if request.method == "POST" and action == "verify_all":
        if not transform_contract:
            messages.error(request, "No active contract configured. Contact administrator.")
            return redirect("projects:ppde_detail", project_id=project.id)
        purpose_payload = {
            "purpose_text": (purpose.value_text or "").strip(),
            "pdo_summary": (purpose.pdo_summary or "").strip(),
            "planning_constraints": (purpose.planning_constraints or "").strip(),
            "assumptions": (purpose.assumptions or "").strip(),
            "cko_alignment_stage1_inputs_match": (purpose.cko_alignment_stage1_inputs_match or "").strip(),
            "cko_alignment_final_outputs_match": (purpose.cko_alignment_final_outputs_match or "").strip(),
        }
        purpose.last_validation = _ppde_validate_block(
            block_key="purpose",
            block_kind="purpose",
            payload=purpose_payload,
            seed_snapshot=seed_snapshot,
            transform_contract=transform_contract,
            user=request.user,
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
                user=request.user,
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
        purpose_payload = {
            "purpose_text": (purpose.value_text or "").strip(),
            "pdo_summary": (purpose.pdo_summary or "").strip(),
            "planning_constraints": (purpose.planning_constraints or "").strip(),
            "assumptions": (purpose.assumptions or "").strip(),
            "cko_alignment_stage1_inputs_match": (purpose.cko_alignment_stage1_inputs_match or "").strip(),
            "cko_alignment_final_outputs_match": (purpose.cko_alignment_final_outputs_match or "").strip(),
        }
        purpose.last_validation = _ppde_validate_block(
            block_key="purpose",
            block_kind="purpose",
            payload=purpose_payload,
            seed_snapshot=seed_snapshot,
            transform_contract=transform_contract,
            user=request.user,
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
                user=request.user,
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
        skipped_locked = 0
        exit_after = bool((request.POST.get("exit") or "").strip())
        purpose_updates = {
            "value_text": (request.POST.get("purpose_text") or "").strip(),
            "pdo_summary": (request.POST.get("pdo_summary") or "").strip(),
            "planning_constraints": (request.POST.get("planning_constraints") or "").strip(),
            "assumptions": (request.POST.get("assumptions") or "").strip(),
            "cko_alignment_stage1_inputs_match": (request.POST.get("cko_alignment_stage1_inputs_match") or "").strip(),
            "cko_alignment_final_outputs_match": (request.POST.get("cko_alignment_final_outputs_match") or "").strip(),
        }
        purpose_changed = False
        for key, val in purpose_updates.items():
            if (getattr(purpose, key) or "").strip() != val:
                purpose_changed = True
                break
        if purpose_changed:
            if exit_after and purpose.status != ProjectPlanningPurpose.Status.DRAFT:
                skipped_locked += 1
            else:
                if can_commit and purpose.status in (ProjectPlanningPurpose.Status.PROPOSED, ProjectPlanningPurpose.Status.PASS_LOCKED):
                    purpose.status = ProjectPlanningPurpose.Status.DRAFT
                    purpose.proposed_by = None
                    purpose.proposed_at = None
                    purpose.locked_by = None
                    purpose.locked_at = None
                    purpose.last_validation = {}
                for key, val in purpose_updates.items():
                    setattr(purpose, key, val)
                purpose.last_edited_by = request.user
                purpose.last_edited_at = timezone.now()
                purpose.save(
                    update_fields=[
                        "value_text",
                        "pdo_summary",
                        "planning_constraints",
                        "assumptions",
                        "cko_alignment_stage1_inputs_match",
                        "cko_alignment_final_outputs_match",
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
            if exit_after and stage.status != ProjectPlanningStage.Status.DRAFT:
                skipped_locked += 1
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
                    "purpose",
                    "inputs",
                    "stage_process",
                    "outputs",
                    "assumptions",
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
        if skipped_locked:
            messages.info(request, f"Skipped {skipped_locked} locked/proposed block(s).")

        if exit_after:
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
            purpose=stage.purpose,
            inputs=stage.inputs,
            stage_process=stage.stage_process,
            outputs=stage.outputs,
            assumptions=stage.assumptions,
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
            if wants_json:
                return JsonResponse({"ok": False, "message": "Missing block key."}, status=400)
            messages.error(request, "Missing block key.")
            return redirect("projects:ppde_detail", project_id=project.id)

        if block_key == "purpose":
            block = ProjectPlanningPurpose.objects.get(project=project)
            def _val_or_current(field_key: str, current: str) -> str:
                raw = request.POST.get(field_key)
                if raw is None:
                    return (current or "").strip()
                return raw.strip()

            proposed_fields = {
                "value_text": _val_or_current("purpose_text", block.value_text),
                "pdo_summary": _val_or_current("pdo_summary", block.pdo_summary),
                "planning_constraints": _val_or_current("planning_constraints", block.planning_constraints),
                "assumptions": _val_or_current("assumptions", block.assumptions),
                "cko_alignment_stage1_inputs_match": _val_or_current(
                    "cko_alignment_stage1_inputs_match",
                    block.cko_alignment_stage1_inputs_match,
                ),
                "cko_alignment_final_outputs_match": _val_or_current(
                    "cko_alignment_final_outputs_match",
                    block.cko_alignment_final_outputs_match,
                ),
            }
            changed = False
            for key, val in proposed_fields.items():
                if (getattr(block, key) or "").strip() != val:
                    changed = True
                    break

            if block.status in (ProjectPlanningPurpose.Status.PROPOSED, ProjectPlanningPurpose.Status.PASS_LOCKED) and not can_commit:
                if wants_json:
                    return JsonResponse({"ok": False, "message": "Only the Project Committer can edit this block."}, status=403)
                messages.error(request, "Only the Project Committer can edit this block.")
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

            if action == "verify_block":
                if not transform_contract:
                    if wants_json:
                        return JsonResponse({"ok": False, "message": "No active contract configured. Contact administrator."}, status=400)
                    messages.error(request, "No active contract configured. Contact administrator.")
                    return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)
                vobj = _ppde_validate_block(
                    block_key=block_key,
                    block_kind="purpose",
                    payload=proposed_fields,
                    seed_snapshot=seed_snapshot,
                    transform_contract=transform_contract,
                    user=request.user,
                )
                block.last_validation = vobj
                block.save(update_fields=["last_validation", "updated_at"])
                request.session["ppde_last_validation_key"] = block_key
                request.session.modified = True
                messages.info(request, "Verification complete.")
                if wants_json:
                    return JsonResponse(
                        {
                            "ok": True,
                            "block_key": block_key,
                            "status": block.status,
                            "validation": vobj,
                            "message": "Verification complete.",
                        }
                    )
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

            if action == "save_block":
                if not changed:
                    if wants_json:
                        return JsonResponse(
                            {
                                "ok": True,
                                "block_key": block_key,
                                "status": block.status,
                                "message": "No changes.",
                            }
                        )
                    messages.info(request, "No changes.")
                    return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

                if can_commit and block.status in (ProjectPlanningPurpose.Status.PROPOSED, ProjectPlanningPurpose.Status.PASS_LOCKED):
                    block.status = ProjectPlanningPurpose.Status.DRAFT
                    block.proposed_by = None
                    block.proposed_at = None
                    block.locked_by = None
                    block.locked_at = None
                    block.last_validation = {}

                for key, val in proposed_fields.items():
                    setattr(block, key, val)
                block.last_edited_by = request.user
                block.last_edited_at = timezone.now()
                block.save(
                    update_fields=[
                        "value_text",
                        "pdo_summary",
                        "planning_constraints",
                        "assumptions",
                        "cko_alignment_stage1_inputs_match",
                        "cko_alignment_final_outputs_match",
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
                if wants_json:
                    return JsonResponse(
                        {
                            "ok": True,
                            "block_key": block_key,
                            "status": block.status,
                            "message": "Changes saved.",
                        }
                    )
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

            if action == "propose_lock":
                for key, val in proposed_fields.items():
                    setattr(block, key, val)
                block.last_edited_by = request.user
                block.last_edited_at = timezone.now()
                block.status = ProjectPlanningPurpose.Status.PROPOSED
                block.proposed_by = request.user
                block.proposed_at = timezone.now()
                block.save(
                    update_fields=[
                        "value_text",
                        "pdo_summary",
                        "planning_constraints",
                        "assumptions",
                        "cko_alignment_stage1_inputs_match",
                        "cko_alignment_final_outputs_match",
                        "last_edited_by",
                        "last_edited_at",
                        "status",
                        "proposed_by",
                        "proposed_at",
                        "updated_at",
                    ]
                )
                messages.success(request, "Lock proposed.")
                if wants_json:
                    return JsonResponse(
                        {
                            "ok": True,
                            "block_key": block_key,
                            "status": "PROPOSED",
                            "proposed_by": request.user.username,
                        }
                    )
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

            if action == "approve_lock":
                if not can_commit:
                    if wants_json:
                        return JsonResponse({"ok": False, "message": "Only the Project Committer can approve."}, status=403)
                    messages.error(request, "Only the Project Committer can approve.")
                    return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)
                if block.status != ProjectPlanningPurpose.Status.PROPOSED:
                    if wants_json:
                        return JsonResponse({"ok": False, "message": "Block is not proposed."}, status=400)
                    messages.error(request, "Block is not proposed.")
                    return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)
                block.status = ProjectPlanningPurpose.Status.PASS_LOCKED
                block.locked_by = request.user
                block.locked_at = timezone.now()
                block.save(update_fields=["status", "locked_by", "locked_at", "updated_at"])
                messages.success(request, "Block locked.")
                if wants_json:
                    return JsonResponse(
                        {
                            "ok": True,
                            "block_key": block_key,
                            "status": "PASS_LOCKED",
                            "locked_by": request.user.username,
                        }
                    )
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

            if action == "reopen_block":
                if not can_commit:
                    if wants_json:
                        return JsonResponse({"ok": False, "message": "Only the Project Committer can reopen."}, status=403)
                    messages.error(request, "Only the Project Committer can reopen.")
                    return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)
                block.status = ProjectPlanningPurpose.Status.DRAFT
                block.proposed_by = None
                block.proposed_at = None
                block.locked_by = None
                block.locked_at = None
                block.save(update_fields=["status", "proposed_by", "proposed_at", "locked_by", "locked_at", "updated_at"])
                messages.success(request, "Block reopened.")
                if wants_json:
                    return JsonResponse(
                        {
                            "ok": True,
                            "block_key": block_key,
                            "status": "DRAFT",
                        }
                    )
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

        if block_key.startswith("stage:"):
            stage_id = block_key.split(":", 1)[1]
            stage = get_object_or_404(ProjectPlanningStage, id=stage_id, project=project)
            payload = _stage_payload_from_request(request)
            if not any(payload.values()):
                payload = _stage_payload_from_model(stage)
            changed = _stage_payload_changed(stage, payload)

            if stage.status in (ProjectPlanningStage.Status.PROPOSED, ProjectPlanningStage.Status.PASS_LOCKED) and not can_commit:
                if wants_json:
                    return JsonResponse({"ok": False, "message": "Only the Project Committer can edit this block."}, status=403)
                messages.error(request, "Only the Project Committer can edit this block.")
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

            if action == "verify_block":
                if not transform_contract:
                    if wants_json:
                        return JsonResponse({"ok": False, "message": "No active contract configured. Contact administrator."}, status=400)
                    messages.error(request, "No active contract configured. Contact administrator.")
                    return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)
                vobj = _ppde_validate_block(
                    block_key=block_key,
                    block_kind="stage",
                    payload=payload,
                    seed_snapshot=seed_snapshot,
                    transform_contract=transform_contract,
                    user=request.user,
                )
                stage.last_validation = vobj
                stage.save(update_fields=["last_validation", "updated_at"])
                request.session["ppde_last_validation_key"] = block_key
                request.session.modified = True
                messages.info(request, "Verification complete.")
                if wants_json:
                    return JsonResponse(
                        {
                            "ok": True,
                            "block_key": block_key,
                            "status": stage.status,
                            "validation": vobj,
                            "message": "Verification complete.",
                        }
                    )
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

            if action == "save_block":
                if not changed:
                    if wants_json:
                        return JsonResponse(
                            {
                                "ok": True,
                                "block_key": block_key,
                                "status": stage.status,
                                "message": "No changes.",
                            }
                        )
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
                        "purpose",
                        "inputs",
                        "stage_process",
                        "outputs",
                        "assumptions",
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
                if wants_json:
                    return JsonResponse(
                        {
                            "ok": True,
                            "block_key": block_key,
                            "status": stage.status,
                            "message": "Changes saved.",
                        }
                    )
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
                        "purpose",
                        "inputs",
                        "stage_process",
                        "outputs",
                        "assumptions",
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
                if wants_json:
                    return JsonResponse(
                        {
                            "ok": True,
                            "block_key": block_key,
                            "status": "PROPOSED",
                            "proposed_by": request.user.username,
                        }
                    )
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

            if action == "approve_lock":
                if not can_commit:
                    if wants_json:
                        return JsonResponse({"ok": False, "message": "Only the Project Committer can approve."}, status=403)
                    messages.error(request, "Only the Project Committer can approve.")
                    return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)
                if stage.status != ProjectPlanningStage.Status.PROPOSED:
                    if wants_json:
                        return JsonResponse({"ok": False, "message": "Block is not proposed."}, status=400)
                    messages.error(request, "Block is not proposed.")
                    return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)
                stage.status = ProjectPlanningStage.Status.PASS_LOCKED
                stage.locked_by = request.user
                stage.locked_at = timezone.now()
                stage.save(update_fields=["status", "locked_by", "locked_at", "updated_at"])
                messages.success(request, "Block locked.")
                if wants_json:
                    return JsonResponse(
                        {
                            "ok": True,
                            "block_key": block_key,
                            "status": "PASS_LOCKED",
                            "locked_by": request.user.username,
                        }
                    )
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

            if action == "reopen_block":
                if not can_commit:
                    if wants_json:
                        return JsonResponse({"ok": False, "message": "Only the Project Committer can reopen."}, status=403)
                    messages.error(request, "Only the Project Committer can reopen.")
                    return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)
                stage.status = ProjectPlanningStage.Status.DRAFT
                stage.proposed_by = None
                stage.proposed_at = None
                stage.locked_by = None
                stage.locked_at = None
                stage.save(update_fields=["status", "proposed_by", "proposed_at", "locked_by", "locked_at", "updated_at"])
                messages.success(request, "Block reopened.")
                if wants_json:
                    return JsonResponse(
                        {
                            "ok": True,
                            "block_key": block_key,
                            "status": "DRAFT",
                        }
                    )
                return redirect(reverse("projects:ppde_detail", kwargs={"project_id": project.id}) + anchor)

    if request.method == "POST" and action == "commit_wko_version":
        messages.info(request, "PPDE outputs a PDO. Use Finalise PDO.")
        return redirect("projects:ppde_detail", project_id=project.id)

    if request.method == "POST" and action == "finalise_pdo":
        if not can_commit:
            messages.error(request, "Only the Project Committer can finalise a PDO.")
            return redirect("projects:ppde_detail", project_id=project.id)

        purpose, stages = _ensure_ppde_blocks(project)
        issues = []

        pdo_summary = (purpose.pdo_summary or "").strip()
        if not pdo_summary:
            issues.append("PDO summary is required.")

        planning_purpose = (purpose.value_text or "").strip()
        if not planning_purpose:
            issues.append("Planning purpose is required.")

        cko_stage1 = (purpose.cko_alignment_stage1_inputs_match or "").strip()
        if not cko_stage1:
            issues.append("CKO alignment (stage 1 inputs match) is required.")

        cko_final = (purpose.cko_alignment_final_outputs_match or "").strip()
        if not cko_final:
            issues.append("CKO alignment (final outputs match) is required.")

        ordered = sorted(stages, key=lambda s: (s.order_index, s.id))
        for idx, stage in enumerate(ordered, start=1):
            if stage.order_index != idx:
                issues.append("Stage numbering is not sequential at stage " + str(stage.order_index) + ".")
                break
            if not (stage.purpose or "").strip():
                issues.append(f"Stage {idx} is missing purpose.")
            if not (stage.inputs or "").strip():
                issues.append(f"Stage {idx} is missing inputs.")
            if not (stage.stage_process or "").strip():
                issues.append(f"Stage {idx} is missing stage process.")
            if not (stage.outputs or "").strip():
                issues.append(f"Stage {idx} is missing outputs.")

        if issues:
            for it in issues:
                messages.error(request, it)
            return redirect("projects:ppde_detail", project_id=project.id)

        payload = {
            "pdo_summary": pdo_summary,
            "cko_alignment": {
                "stage1_inputs_match": cko_stage1,
                "final_outputs_match": cko_final,
            },
            "planning_purpose": planning_purpose,
            "planning_constraints": (purpose.planning_constraints or "").strip(),
            "assumptions": (purpose.assumptions or "").strip(),
            "stages": [],
        }

        for idx, stage in enumerate(ordered, start=1):
            payload["stages"].append(
                {
                    "stage_number": idx,
                    "status": stage.status,
                    "title": (stage.title or "").strip(),
                    "purpose": (stage.purpose or "").strip(),
                    "inputs": (stage.inputs or "").strip(),
                    "stage_process": (stage.stage_process or "").strip(),
                    "outputs": (stage.outputs or "").strip(),
                    "assumptions": (stage.assumptions or "").strip(),
                    "duration_estimate": (stage.duration_estimate or "").strip(),
                    "risks_notes": (stage.risks_notes or "").strip(),
                }
            )

        latest = ProjectPDO.objects.filter(project=project).aggregate(Max("version")).get("version__max") or 0
        ProjectPDO.objects.create(
            project=project,
            version=latest + 1,
            status=ProjectPDO.Status.DRAFT,
            seed_snapshot=seed_snapshot,
            content_json=payload,
            change_summary="Finalised from PPDE",
            created_by=request.user,
        )
        messages.success(request, "PDO finalised.")
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

    topic_chats = list(
        ProjectTopicChat.objects.filter(project=project, user=request.user, scope="PPDE")
        .select_related("chat")
    )
    topic_chat_map = {tc.topic_key: tc.chat_id for tc in topic_chats}
    topic_chat_ids = set(topic_chat_map.values())
    purpose_chat_id = topic_chat_map.get("PURPOSE")
    stage_chat_ids: Dict[int, int] = {}
    for key, cid in topic_chat_map.items():
        if key.startswith("STAGE:"):
            sid = key.split(":", 1)[1]
            if sid.isdigit():
                stage_chat_ids[int(sid)] = cid

    stage_specs: List[Dict[str, Any]] = []
    for stage in stages:
        stage_specs.append(
            {
                "id": stage.id,
                "order_index": stage.order_index,
                "title": stage.title,
                "purpose": stage.purpose,
                "inputs": stage.inputs,
                "stage_process": stage.stage_process,
                "outputs": stage.outputs,
                "assumptions": stage.assumptions,
                "duration_estimate": stage.duration_estimate,
                "risks_notes": stage.risks_notes,
                "status": stage.status,
                "proposed_by": (getattr(stage.proposed_by, "username", "") or ""),
                "locked_by": (getattr(stage.locked_by, "username", "") or ""),
                "last_validation": stage.last_validation or {},
                "validation_key": "stage:" + str(stage.id),
                "topic_chat_id": stage_chat_ids.get(stage.id),
            }
        )

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
        ppde_status_badge = {"text": "Ready to Finalise", "class": "bg-success"}

    stage_preview = request.session.get(_ppde_stage_preview_key(project.id))
    stage_edit_log = _get_ppde_stage_edit_log(request, project.id)
    stage_edit_auto_open = bool(request.session.get("ppde_stage_edit_auto_open_" + str(project.id)))

    ppde_chat_id_raw = (request.GET.get("ppde_chat_id") or "").strip()
    if not ppde_chat_id_raw:
        ppde_chat_id_raw = str(request.session.get("ppde_drawer_chat_id") or "")

    open_param = (request.GET.get("ppde_chat_open") or "").strip()
    if open_param in ("0", "1"):
        request.session["ppde_drawer_open"] = (open_param == "1")
        request.session.modified = True

    selected_chat_id = int(ppde_chat_id_raw) if ppde_chat_id_raw.isdigit() else None
    if selected_chat_id is not None:
        request.session["ppde_drawer_chat_id"] = selected_chat_id
        request.session.modified = True

    chat_ids = set()
    if purpose_chat_id:
        chat_ids.add(purpose_chat_id)
    for s in stage_specs:
        if s.get("topic_chat_id"):
            chat_ids.add(s["topic_chat_id"])

    chat_ctx_map = {}
    if chat_ids:
        chats = {c.id: c for c in ChatWorkspace.objects.filter(id__in=chat_ids)}
        for chat_id, chat in chats.items():
            ctx = build_chat_turn_context(request, chat)
            qs = request.GET.copy()
            qs["ppde_chat_id"] = str(chat.id)
            qs["ppde_chat_open"] = "1"
            qs.pop("turn", None)
            qs.pop("system", None)
            ctx["chat"] = chat
            if purpose_chat_id and chat.id == purpose_chat_id:
                ctx["apply_target"] = "ppde_purpose"
            ctx["qs_base"] = qs.urlencode()
            if selected_chat_id == chat.id:
                if open_param in ("0", "1"):
                    ctx["is_open"] = (open_param == "1")
                else:
                    ctx["is_open"] = bool(request.session.get("ppde_drawer_open"))
            else:
                ctx["is_open"] = False
            chat_ctx_map[chat.id] = ctx

    purpose_chat_ctx = chat_ctx_map.get(purpose_chat_id)
    for s in stage_specs:
        s["topic_chat_ctx"] = chat_ctx_map.get(s.get("topic_chat_id"))

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
        "seed_context": seed_context,
        "seed_view": seed_view,
        "seed_has_summary": bool(isinstance(project.ppde_seed_summary, dict) and project.ppde_seed_summary),
        "ui_return_to": reverse("accounts:project_config_info", kwargs={"project_id": project.id}),
        "ppde_help_log": ppde_help_log,
        "ppde_help_auto_open": ppde_help_auto_open,
        "purpose": {
            "value_text": purpose.value_text,
            "pdo_summary": purpose.pdo_summary,
            "planning_constraints": purpose.planning_constraints,
            "assumptions": purpose.assumptions,
            "cko_alignment_stage1_inputs_match": purpose.cko_alignment_stage1_inputs_match,
            "cko_alignment_final_outputs_match": purpose.cko_alignment_final_outputs_match,
            "status": purpose.status,
            "proposed_by": (getattr(purpose.proposed_by, "username", "") or ""),
            "locked_by": (getattr(purpose.locked_by, "username", "") or ""),
            "last_validation": purpose.last_validation or {},
        },
        "stages": stage_specs,
        "show_validation_key": show_validation_key,
        "purpose_chat_id": purpose_chat_id,
        "purpose_chat_ctx": purpose_chat_ctx,
        "stage_preview": stage_preview,
        "stage_edit_log": stage_edit_log,
        "stage_edit_auto_open": stage_edit_auto_open,
    }

    return render(request, "projects/ppde_detail.html", context)
