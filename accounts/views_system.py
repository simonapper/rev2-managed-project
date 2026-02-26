# -*- coding: utf-8 -*-
# accounts/views_system.py

from __future__ import annotations

import json
from collections import OrderedDict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.http import HttpResponse
from django.http import Http404
from django.http import JsonResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from config.models import ConfigRecord, ConfigVersion, SystemConfigPointers, ConfigScope
from chats.models import ChatWorkspace, ContractOverride, ContractText
from chats.services.contracts.inspect import get_raw_contract_text
from chats.services.contracts.pipeline import ContractContext
from chats.services.llm import generate_text
from chats.services.contracts.phase_resolver import resolve_phase_contract
from chats.services.contracts.texts import (
    CONTRACT_TEXT_LABELS,
    normalise_contract_text_key,
    resolve_contract_text,
)
from projects.models import PhaseContract, Project, WorkItem
from projects.services.llm_instructions import PROTOCOL_LIBRARY_V2
from projects.services.context_resolution import resolve_effective_context
from projects.services_project_membership import accessible_projects_qs


def _require_superuser(request) -> None:
    if not request.user.is_superuser:
        raise Http404()


def _get_pointers() -> SystemConfigPointers:
    obj, _ = SystemConfigPointers.objects.get_or_create(pk=1)
    return obj


def _level_label(level: int) -> str:
    try:
        return ConfigRecord.Level(level).label
    except Exception:
        return f"Level {level}"


def _pointer_field_for_level(level: int) -> str:
    mapping = {
        ConfigRecord.Level.L1: "active_l1_config",
        ConfigRecord.Level.L2: "active_l2_config",
        ConfigRecord.Level.L3: "active_l3_config",
        ConfigRecord.Level.L4: "active_l4_config",
    }
    try:
        return mapping[ConfigRecord.Level(level)]
    except Exception:
        raise Http404("Invalid level.")


def _phase_contract_seed_data() -> list[dict]:
    return [
        {
            "key": "STRUCTURE_PROJECT",
            "title": "PPDE Contract - Structure Project (CKO to Stage Map)",
            "version": 1,
            "is_active": True,
            "purpose_text": (
                "Produce a practical stage map from the accepted CKO. "
                "The stage map must be suitable for PPDE refinement and later planning."
            ),
            "inputs_text": (
                "Accepted CKO snapshot (fields as available).\n"
                "Seed summary if present.\n"
                "Project constraints from the accepted CKO.\n"
                "Optional prior PPDE working draft context (read-only)."
            ),
            "outputs_text": (
                "JSON only: {\"stages\":[...]} matching the stage-map schema.\n"
                "3 to 8 stages preferred.\n"
                "Each stage includes title, description, purpose, entry_condition, "
                "acceptance_statement, exit_condition, key_deliverables, "
                "duration_estimate, risks_notes."
            ),
            "method_guidance_text": (
                "Derive stages top-down from the CKO goal and acceptance test.\n"
                "Make stages non-overlapping and sequential where sensible.\n"
                "Keep each stage concrete.\n"
                "Entry condition: what must be true to start.\n"
                "Acceptance statement: verifiable completion statement.\n"
                "Exit condition: what is true when leaving the stage.\n"
                "Use language that a small team can execute.\n"
                "If the CKO is vague, make reasonable assumptions and record them in risks_notes."
            ),
            "acceptance_test_text": (
                "Output is valid JSON only and matches schema.\n"
                "Every stage has a non-empty title and acceptance_statement.\n"
                "key_deliverables has at least one item per stage.\n"
                "Stages are not duplicates and do not substantially overlap.\n"
                "Stages collectively cover the project from start to finish."
            ),
            "llm_review_prompt_text": (
                "Check the stage map for overlap, gaps, and unverifiable acceptance statements.\n"
                "Ensure each stage is concrete and contains at least one deliverable.\n"
                "If any stage is too vague, rewrite it to be executable."
            ),
            "policy_json": {
                "output_format": "json_only",
                "preferred_stage_count_min": 3,
                "preferred_stage_count_max": 8,
                "require_non_overlapping_stages": True,
                "require_verifiable_acceptance": True,
                "require_key_deliverables_min": 1,
                "tone": "brief_executable",
                "notes": "Used for committer-only stage-map generation from accepted CKO.",
            },
        },
        {
            "key": "TRANSFORM_STAGE",
            "title": "PPDE Contract - Transform Stage (Refine and Verify)",
            "version": 1,
            "is_active": True,
            "purpose_text": (
                "Improve a single stage so it is clear, testable, and executable. "
                "Produce stage text that supports downstream planning."
            ),
            "inputs_text": (
                "Current stage object (all stage fields).\n"
                "Neighbouring stage titles if available.\n"
                "Project planning purpose text.\n"
                "Project constraints from accepted CKO or seed summary."
            ),
            "outputs_text": (
                "JSON only: a single stage object with the same keys as the stage schema.\n"
                "Fields may be revised for clarity but intent must be preserved."
            ),
            "method_guidance_text": (
                "Make acceptance_statement verifiable.\n"
                "Ensure entry_condition and exit_condition are concrete and distinct.\n"
                "Ensure key_deliverables are tangible outcomes, not vague activities.\n"
                "Keep description concise; move uncertainty into risks_notes.\n"
                "Avoid overlap with neighbouring stages where context is available.\n"
                "Prefer plain language; avoid jargon."
            ),
            "acceptance_test_text": (
                "Output is valid JSON only and matches the stage schema.\n"
                "title and acceptance_statement are non-empty.\n"
                "key_deliverables contains at least one non-empty string.\n"
                "entry_condition, acceptance_statement, and exit_condition are not redundant.\n"
                "The stage is executable by a team without extra interpretation."
            ),
            "llm_review_prompt_text": (
                "Review the revised stage for unverifiable acceptance statements, "
                "vague deliverables, overlap with other stages, and missing risks.\n"
                "Rewrite weak parts to be concrete and testable."
            ),
            "policy_json": {
                "output_format": "json_only",
                "require_verifiable_acceptance": True,
                "require_key_deliverables_min": 1,
                "allow_boundary_adjustment": True,
                "tone": "brief_executable",
                "notes": "Used for per-stage verify and contributor propose-lock workflow.",
            },
        },
    ]


@login_required
def system_settings_home(request):
    _require_superuser(request)

    pointers = _get_pointers()

    active = {
        1: pointers.active_l1_config,
        2: pointers.active_l2_config,
        3: pointers.active_l3_config,
        4: pointers.active_l4_config,
    }

    return render(
        request,
        "accounts/system/system_settings_home.html",
        {
            "active": active,
        },
    )


@login_required
def system_avatars_catalogue(request):
    _require_superuser(request)

    project = None
    project_id_raw = str(request.GET.get("project_id") or request.session.get("rw_active_project_id") or "").strip()
    if project_id_raw.isdigit():
        project = accessible_projects_qs(request.user).filter(id=int(project_id_raw)).first()
    if project is None:
        project = accessible_projects_qs(request.user).order_by("name", "id").first()

    active_level4 = {}
    if project is not None:
        try:
            effective = resolve_effective_context(
                project_id=project.id,
                user_id=request.user.id,
                session_overrides={},
                chat_overrides={},
            )
            active_level4 = dict((effective or {}).get("level4") or {})
        except Exception:
            active_level4 = {}

    rows = []
    for axis in ("tone", "reasoning", "approach", "control"):
        presets = dict(PROTOCOL_LIBRARY_V2.get(axis) or {})
        for preset_name in sorted(presets.keys()):
            lines = [str(v) for v in (presets.get(preset_name) or []) if str(v).strip()]
            rows.append(
                {
                    "axis": axis,
                    "preset_name": preset_name,
                    "preview": "\n".join(lines),
                }
            )

    return render(
        request,
        "accounts/system/avatars_catalogue.html",
        {
            "rows": rows,
            "project": project,
            "active_level4": active_level4,
        },
    )


@login_required
def system_level_pick(request, level: int):
    """
    Select which ORG-scoped ConfigRecord is ACTIVE for a given level.
    """
    _require_superuser(request)

    # Validate level and resolve pointer field
    pointer_field = _pointer_field_for_level(level)
    level_int = int(level)

    pointers = _get_pointers()

    # Eligible records: ORG scope only, ACTIVE only, correct level
    eligible = (
        ConfigRecord.objects
        .select_related("scope")
        .filter(
            level=level_int,
            status=ConfigRecord.Status.ACTIVE,
            scope__scope_type=ConfigScope.ScopeType.ORG,
        )
        .order_by("display_name", "file_id")
    )

    if request.method == "POST":
        selected_id = request.POST.get("config_id") or ""
        if not selected_id:
            messages.error(request, "Select a config.")
            return redirect(reverse("accounts:system_settings_level_pick", args=[level_int]))

        try:
            selected_pk = int(selected_id)
        except ValueError:
            messages.error(request, "Invalid selection.")
            return redirect(reverse("accounts:system_settings_level_pick", args=[level_int]))

        selected = eligible.filter(pk=selected_pk).first()
        if not selected:
            messages.error(request, "That config is not eligible for system defaults.")
            return redirect(reverse("accounts:system_settings_level_pick", args=[level_int]))

        setattr(pointers, pointer_field, selected)
        pointers.updated_by = request.user
        pointers.save(update_fields=[pointer_field, "updated_by", "updated_at"])

        messages.success(request, f"{_level_label(level_int)} active config set.")
        return redirect("accounts:system_settings_home")

    current = getattr(pointers, pointer_field)

    return render(
        request,
        "accounts/system/system_level_pick.html",
        {
            "level": level_int,
            "level_label": _level_label(level_int),
            "eligible": eligible,
            "current": current,
        },
    )


@login_required
def system_config_detail(request, config_id: int):
    _require_superuser(request)

    cfg = get_object_or_404(
        ConfigRecord.objects.select_related("scope", "created_by"),
        pk=config_id,
    )

    # Only allow browsing ORG configs from this system surface
    if cfg.scope.scope_type != ConfigScope.ScopeType.ORG:
        raise Http404()

    versions = (
        ConfigVersion.objects
        .filter(config=cfg)
        .select_related("created_by")
        .order_by("-created_at")
    )

    latest = versions.first()

    return render(
        request,
        "accounts/system/system_config_detail.html",
        {
            "cfg": cfg,
            "versions": versions,
            "latest": latest,
        },
    )


@login_required
def system_config_version_new(request, config_id: int):
    _require_superuser(request)

    cfg = get_object_or_404(
        ConfigRecord.objects.select_related("scope"),
        pk=config_id,
    )

    if cfg.scope.scope_type != ConfigScope.ScopeType.ORG:
        raise Http404()

    if request.method == "POST":
        version = (request.POST.get("version") or "").strip() or "0.0.0"
        change_note = (request.POST.get("change_note") or "").strip()
        content_text = request.POST.get("content_text") or ""

        if not content_text.strip():
            messages.error(request, "Content cannot be empty.")
            return redirect(reverse("accounts:system_config_version_new", args=[cfg.id]))

        # Prevent duplicate (config, version)
        if ConfigVersion.objects.filter(config=cfg, version=version).exists():
            messages.error(request, "That version already exists for this config.")
            return redirect(reverse("accounts:system_config_version_new", args=[cfg.id]))

        ConfigVersion.objects.create(
            config=cfg,
            version=version,
            content_text=content_text,
            change_note=change_note,
            created_by=request.user,
        )

        messages.success(request, "New version created.")
        return redirect(reverse("accounts:system_config_detail", args=[cfg.id]))

    # Suggest next version (simple: repeat latest or default)
    latest = (
        ConfigVersion.objects
        .filter(config=cfg)
        .order_by("-created_at")
        .first()
    )
    suggested_version = latest.version if latest else "0.0.0"
    suggested_content = latest.content_text if latest else ""

    return render(
        request,
        "accounts/system/system_config_version_new.html",
        {
            "cfg": cfg,
            "suggested_version": suggested_version,
            "suggested_content": suggested_content,
        },
    )


@login_required
def system_phase_contracts_home(request):
    _require_superuser(request)

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()
        if action == "seed_defaults":
            created = 0
            for seed in _phase_contract_seed_data():
                exists = PhaseContract.objects.filter(
                    key=seed["key"],
                    version=seed["version"],
                ).exists()
                if exists:
                    continue
                PhaseContract.objects.create(
                    key=seed["key"],
                    title=seed["title"],
                    version=seed["version"],
                    is_active=seed.get("is_active", False),
                    purpose_text=seed.get("purpose_text", ""),
                    inputs_text=seed.get("inputs_text", ""),
                    outputs_text=seed.get("outputs_text", ""),
                    method_guidance_text=seed.get("method_guidance_text", ""),
                    acceptance_test_text=seed.get("acceptance_test_text", ""),
                    llm_review_prompt_text=seed.get("llm_review_prompt_text", ""),
                    policy_json=seed.get("policy_json", {}),
                    created_by=request.user,
                )
                created += 1

            if created:
                messages.success(request, f"Seeded {created} phase contracts.")
            else:
                messages.info(request, "Seed contracts already exist.")

            for seed in _phase_contract_seed_data():
                if seed.get("is_active"):
                    PhaseContract.objects.filter(key=seed["key"]).exclude(
                        version=seed["version"],
                    ).update(is_active=False)
            return redirect("accounts:system_phase_contracts_home")

        if action == "activate":
            contract_id = request.POST.get("contract_id") or ""
            try:
                contract = PhaseContract.objects.get(pk=int(contract_id))
            except Exception:
                messages.error(request, "Invalid contract selection.")
                return redirect("accounts:system_phase_contracts_home")

            PhaseContract.objects.filter(key=contract.key).update(is_active=False)
            contract.is_active = True
            contract.save(update_fields=["is_active"])
            messages.success(request, f"Activated {contract.key} v{contract.version}.")
            return redirect("accounts:system_phase_contracts_home")

    contracts = PhaseContract.objects.all().order_by("key", "-version", "-id")
    active_by_key = {}
    for contract in contracts:
        if contract.is_active and contract.key not in active_by_key:
            active_by_key[contract.key] = contract

    return render(
        request,
        "accounts/system/system_phase_contracts_home.html",
        {
            "contracts": contracts,
            "active_by_key": active_by_key,
        },
    )


@login_required
def system_phase_contract_edit(request, contract_id: int | None = None):
    _require_superuser(request)

    contract = None
    if contract_id is not None:
        contract = get_object_or_404(PhaseContract, pk=contract_id)

    copy_from = None
    copy_id = request.GET.get("copy_from") or ""
    if not contract and copy_id:
        try:
            copy_from = PhaseContract.objects.get(pk=int(copy_id))
        except Exception:
            copy_from = None

    if request.method == "POST":
        key = (request.POST.get("key") or "").strip()
        title = (request.POST.get("title") or "").strip()
        version_raw = (request.POST.get("version") or "").strip()
        is_active = request.POST.get("is_active") == "on"
        purpose_text = request.POST.get("purpose_text") or ""
        inputs_text = request.POST.get("inputs_text") or ""
        outputs_text = request.POST.get("outputs_text") or ""
        method_guidance_text = request.POST.get("method_guidance_text") or ""
        acceptance_test_text = request.POST.get("acceptance_test_text") or ""
        llm_review_prompt_text = request.POST.get("llm_review_prompt_text") or ""
        policy_text = request.POST.get("policy_json") or ""

        if not key or not title:
            messages.error(request, "Key and title are required.")
            return redirect(request.path)

        try:
            version = int(version_raw or "1")
        except ValueError:
            messages.error(request, "Version must be an integer.")
            return redirect(request.path)

        try:
            policy_json = json.loads(policy_text) if policy_text.strip() else {}
        except Exception:
            messages.error(request, "Policy JSON is invalid.")
            return redirect(request.path)

        if contract is None and PhaseContract.objects.filter(key=key, version=version).exists():
            messages.error(request, "That key/version already exists.")
            return redirect(request.path)

        if contract is None:
            contract = PhaseContract.objects.create(
                key=key,
                title=title,
                version=version,
                is_active=is_active,
                purpose_text=purpose_text,
                inputs_text=inputs_text,
                outputs_text=outputs_text,
                method_guidance_text=method_guidance_text,
                acceptance_test_text=acceptance_test_text,
                llm_review_prompt_text=llm_review_prompt_text,
                policy_json=policy_json,
                created_by=request.user,
            )
        else:
            contract.key = key
            contract.title = title
            contract.version = version
            contract.is_active = is_active
            contract.purpose_text = purpose_text
            contract.inputs_text = inputs_text
            contract.outputs_text = outputs_text
            contract.method_guidance_text = method_guidance_text
            contract.acceptance_test_text = acceptance_test_text
            contract.llm_review_prompt_text = llm_review_prompt_text
            contract.policy_json = policy_json
            contract.save()

        if is_active:
            PhaseContract.objects.filter(key=contract.key).exclude(pk=contract.id).update(is_active=False)

        messages.success(request, "Phase contract saved.")
        return redirect("accounts:system_phase_contracts_home")

    seed = copy_from or contract
    initial_policy = "{}"
    if seed and isinstance(seed.policy_json, dict):
        try:
            initial_policy = json.dumps(seed.policy_json, indent=2, ensure_ascii=True)
        except Exception:
            initial_policy = "{}"

    initial_version = ""
    if copy_from:
        initial_version = str(copy_from.version + 1)
    elif contract:
        initial_version = str(contract.version)

    is_active_checked = False
    if contract:
        is_active_checked = contract.is_active
    elif seed and not copy_from:
        is_active_checked = seed.is_active

    return render(
        request,
        "accounts/system/system_phase_contract_edit.html",
        {
            "contract": contract,
            "seed": seed,
            "initial_version": initial_version,
            "initial_policy": initial_policy,
            "is_active_checked": is_active_checked,
        },
    )


def _contracts_context_from_request(request) -> ContractContext:
    project = None
    chat = None
    work_item = None
    project_id = request.GET.get("project_id") or request.session.get("rw_active_project_id")
    chat_id = request.GET.get("chat_id") or request.session.get("rw_active_chat_id")
    if str(project_id or "").isdigit():
        project = Project.objects.filter(id=int(project_id)).first()
    if str(chat_id or "").isdigit():
        chat = ChatWorkspace.objects.filter(id=int(chat_id)).first()
        if project is None and chat is not None:
            project = chat.project
    effective_context = {}
    if project is not None:
        try:
            effective_context = dict(
                resolve_effective_context(
                    project_id=project.id,
                    user_id=request.user.id,
                    session_overrides={},
                    chat_overrides={},
                )
                or {}
            )
        except Exception:
            effective_context = {}
        work_item = WorkItem.objects.filter(project=project).order_by("-updated_at", "-id").first()
    return ContractContext(
        user=request.user,
        chat=chat,
        project=project,
        work_item=work_item,
        active_phase=(getattr(work_item, "active_phase", "") or ""),
        effective_context=effective_context,
        is_cde=bool(chat),
    )


_DASHBOARD_TEXT_KEYS = (
    "language",
    "tone",
    "reasoning",
    "approach",
    "control",
    "phase.define",
    "phase.explore",
    "phase.refine",
    "phase.approve",
    "phase.execute",
    "phase.complete",
)


_DASHBOARD_DERIVED_KEYS = (
    "phase.contract",
    "active_avatars",
    "boundary.profile",
    "cde.contract",
)

_DASHBOARD_VERIFICATION_KEYS = (
    "pde.validator.boilerplate",
    "pde.draft.boilerplate",
    "cde.validator.boilerplate",
    "cde.draft.boilerplate",
    "cko.review.system_block",
)

_USER_OVERRIDE_KEYS = tuple(list(_DASHBOARD_TEXT_KEYS) + ["boundary.profile"])
_PREVIEW_DEFAULT_PROMPT = "Assess the situation between the UK and Europe."


_RAW_SOURCE_BY_KEY = {
    "language": "avatars.protocol.0",
    "tone": "avatars.protocol.1",
    "reasoning": "avatars.protocol.2",
    "approach": "avatars.protocol.3",
    "control": "avatars.protocol.4",
    "active_avatars": "avatars.protocol.5",
    "phase.contract": "phase.contract",
    "phase.define": "phase.define",
    "phase.explore": "phase.explore",
    "phase.refine": "phase.refine",
    "phase.approve": "phase.approve",
    "phase.execute": "phase.execute",
    "phase.complete": "phase.complete",
    "cde.contract": "cde.contract.0",
    "pde.validator.boilerplate": "pde.validator.boilerplate",
    "pde.draft.boilerplate": "pde.draft.boilerplate",
    "cde.validator.boilerplate": "cde.validator.boilerplate",
    "cde.draft.boilerplate": "cde.draft.boilerplate",
    "cko.review.system_block": "cko.review.system_block",
}


_USAGE_HINTS_BY_KEY = {
    "pde.validator.boilerplate": {
        "used_in": "projects.services.pde.validate_field",
        "flows": "PDE verify",
    },
    "pde.draft.boilerplate": {
        "used_in": "projects.services.pde.draft_pde_from_seed",
        "flows": "PDE draft/seed",
    },
    "cde.validator.boilerplate": {
        "used_in": "chats.services.cde.validate_field",
        "flows": "CDE verify",
    },
    "cde.draft.boilerplate": {
        "used_in": "chats.services.cde.draft_cde_from_seed",
        "flows": "CDE draft/seed",
    },
    "cko.review.system_block": {
        "used_in": "projects.views_review._build_intent_seed_from_cko_llm",
        "flows": "Review INTENT/CKO seed",
    },
    "phase.contract": {
        "used_in": "chats.services.contracts.phase_resolver.resolve_phase_contract",
        "flows": "Main prompt injection",
    },
}


def _active_level4_for_dashboard(ctx: ContractContext, user) -> dict:
    project = getattr(ctx, "project", None)
    if project is None or user is None:
        return {}
    try:
        effective = resolve_effective_context(
            project_id=project.id,
            user_id=user.id,
            session_overrides={},
            chat_overrides={},
        )
        return dict((effective or {}).get("level4") or {})
    except Exception:
        return {}


def _preset_axis_for_key(key: str) -> str:
    mapping = {
        "tone": "tone",
        "reasoning": "reasoning",
        "approach": "approach",
        "control": "control",
    }
    return mapping.get(str(key or "").strip().lower(), "")


def _preset_options_for_key(key: str, active_level4: dict) -> tuple[str, str, list[dict]]:
    axis = _preset_axis_for_key(key)
    if not axis:
        return "", "", []
    presets = dict(PROTOCOL_LIBRARY_V2.get(axis) or {})
    active_name = str(active_level4.get(axis) or "").strip()
    options = []
    for preset_name in sorted(presets.keys()):
        lines = [str(v) for v in (presets.get(preset_name) or []) if str(v).strip()]
        options.append(
            {
                "name": preset_name,
                "preview": "\n".join(lines),
                "is_active": bool(active_name and preset_name == active_name),
            }
        )
    return axis, active_name, options


def _raw_contract_default(ctx: ContractContext, key: str) -> str:
    raw_key = _RAW_SOURCE_BY_KEY.get(key)
    if not raw_key:
        return ""
    return str(get_raw_contract_text(ctx, raw_key) or "")


def _preview_override_block(key: str, text: str) -> str:
    k = str(key or "").strip().lower()
    body = str(text or "").strip()
    if not k or not body:
        return ""
    return (
        "CONTRACT PREVIEW OVERRIDE\n"
        f"Key: {k}\n"
        "Use this contract text for this response only.\n"
        "Do not mention this override in the answer.\n\n"
        + body
    ).strip()


def _preview_contract_ctx(ctx: ContractContext, key: str, text: str) -> ContractContext:
    legacy = list(getattr(ctx, "legacy_system_blocks", []) or [])
    override_block = _preview_override_block(key, text)
    if override_block:
        legacy.append(override_block)
    return ContractContext(
        user=getattr(ctx, "user", None),
        chat=getattr(ctx, "chat", None),
        project=getattr(ctx, "project", None),
        work_item=getattr(ctx, "work_item", None),
        active_phase=str(getattr(ctx, "active_phase", "") or ""),
        user_text=str(getattr(ctx, "user_text", "") or ""),
        effective_context=dict(getattr(ctx, "effective_context", {}) or {}),
        boundary_excerpts=list(getattr(ctx, "boundary_excerpts", []) or []),
        ppde_phase_contract=getattr(ctx, "ppde_phase_contract", None),
        is_rollup=bool(getattr(ctx, "is_rollup", False)),
        is_review=bool(getattr(ctx, "is_review", False)),
        is_pde=bool(getattr(ctx, "is_pde", False)),
        is_ppde=bool(getattr(ctx, "is_ppde", False)),
        is_cde=bool(getattr(ctx, "is_cde", False)),
        tier5_blocks=list(getattr(ctx, "tier5_blocks", []) or []),
        tier6_blocks=list(getattr(ctx, "tier6_blocks", []) or []),
        legacy_system_blocks=legacy,
        include_envelope=False,
        strict_json=False,
    )


def _resolved_from(ctx: ContractContext, key: str) -> str:
    if key == "phase.contract":
        phase = resolve_phase_contract(ctx)
        contract_id = str(getattr(phase, "effective_phase_contract", "") or "")
        source = str(getattr(phase, "source", "") or "")
        if source == "ppde" and contract_id:
            return f"ppde:{contract_id}"
        if contract_id.startswith("workitem:"):
            phase_name = contract_id.split(":", 1)[1].strip().lower()
            if phase_name:
                return f"phase.{phase_name}"
        if contract_id:
            return contract_id
    if key == "active_avatars":
        return "avatars.protocol.5"
    if key == "cde.contract":
        return "cde.contract.0"
    if key == "boundary.profile":
        return "work_item.boundary_profile_json|project.boundary_profile_json|chat.boundary_profile_json"
    return key


def _effective_boundary_profile(ctx: ContractContext) -> dict:
    work_item = getattr(ctx, "work_item", None)
    project = getattr(ctx, "project", None)
    chat = getattr(ctx, "chat", None)
    merged = {}
    if project is not None and isinstance(getattr(project, "boundary_profile_json", None), dict):
        merged.update(dict(project.boundary_profile_json or {}))
    if chat is not None and isinstance(getattr(chat, "boundary_profile_json", None), dict):
        merged.update(dict(chat.boundary_profile_json or {}))
    if work_item is not None and isinstance(getattr(work_item, "boundary_profile_json", None), dict):
        merged.update(dict(work_item.boundary_profile_json or {}))
    return merged


def _save_boundary_profile(ctx: ContractContext, profile: dict, user) -> str:
    work_item = getattr(ctx, "work_item", None)
    if work_item is not None:
        work_item.boundary_profile_json = dict(profile or {})
        work_item.save(update_fields=["boundary_profile_json", "updated_at"])
        return "work_item"
    project = getattr(ctx, "project", None)
    if project is not None:
        project.boundary_profile_json = dict(profile or {})
        project.save(update_fields=["boundary_profile_json", "updated_at"])
        return "project"
    return ""


@login_required
def system_contracts_dashboard(request):
    ctx = _contracts_context_from_request(request)
    active_level4 = _active_level4_for_dashboard(ctx, request.user)
    project_ctx_id = int(getattr(getattr(ctx, "project", None), "id", 0) or 0)
    project_options = list(accessible_projects_qs(request.user).order_by("name", "id"))
    selected_project = None
    for row in project_options:
        if int(getattr(row, "id", 0) or 0) == project_ctx_id:
            selected_project = row
            break

    if request.method == "POST":
        action = str(request.POST.get("action") or "").strip().lower()
        key = str(request.POST.get("key") or "").strip()
        if action == "preview_contract":
            preview_prompt = str(request.POST.get("preview_prompt") or "").strip() or _PREVIEW_DEFAULT_PROMPT
            preview_text = str(request.POST.get("preview_contract_text") or "")
            try:
                preview_ctx = _preview_contract_ctx(ctx, key, preview_text)
                out = generate_text(
                    system_blocks=[],
                    messages=[{"role": "user", "content": preview_prompt}],
                    user=request.user,
                    contract_ctx=preview_ctx,
                )
                return JsonResponse(
                    {
                        "ok": True,
                        "preview_text": str(out or "").strip(),
                    }
                )
            except Exception as exc:
                return JsonResponse(
                    {
                        "ok": False,
                        "error": str(exc),
                    },
                    status=400,
                )

        if action == "save_boundary_profile":
            raw = str(request.POST.get("boundary_profile_json") or "").strip()
            try:
                payload = json.loads(raw) if raw else {}
            except Exception:
                messages.error(request, "Boundary profile JSON is invalid.")
                return redirect("accounts:system_contracts_dashboard")
            if not isinstance(payload, dict):
                messages.error(request, "Boundary profile must be a JSON object.")
                return redirect("accounts:system_contracts_dashboard")
            target = _save_boundary_profile(ctx, payload, request.user)
            if target:
                messages.success(request, f"Boundary profile saved to {target}.")
            else:
                messages.error(request, "No active project/work item to save boundary profile.")
            return redirect("accounts:system_contracts_dashboard?key=boundary.profile")

        if action == "reset_boundary_profile":
            target = _save_boundary_profile(ctx, {}, request.user)
            if target:
                messages.success(request, f"Boundary profile reset on {target}.")
            else:
                messages.error(request, "No active project/work item to reset boundary profile.")
            return redirect("accounts:system_contracts_dashboard?key=boundary.profile")

        try:
            contract_key = normalise_contract_text_key(key)
        except ValueError:
            contract_key = ""

        if action == "save_user" and contract_key in _DASHBOARD_TEXT_KEYS:
            text = str(request.POST.get("user_text") or "")
            if project_ctx_id > 0:
                row = (
                    ContractText.objects.filter(
                        key=contract_key,
                        scope_type=ContractText.ScopeType.PROJECT,
                        scope_project_id=project_ctx_id,
                        status=ContractText.Status.ACTIVE,
                    )
                    .order_by("-updated_at", "-id")
                    .first()
                )
            else:
                row = (
                    ContractText.objects.filter(
                        key=contract_key,
                        scope_type=ContractText.ScopeType.USER,
                        status=ContractText.Status.ACTIVE,
                    )
                    .filter(Q(scope_user_id=request.user.id) | Q(scope_id=request.user.id))
                    .order_by("-updated_at", "-id")
                    .first()
                )
            if row is None:
                if project_ctx_id > 0:
                    ContractText.objects.create(
                        key=contract_key,
                        scope_type=ContractText.ScopeType.PROJECT,
                        scope_project_id=project_ctx_id,
                        status=ContractText.Status.ACTIVE,
                        text=text,
                        updated_by=request.user,
                    )
                else:
                    ContractText.objects.create(
                        key=contract_key,
                        scope_type=ContractText.ScopeType.USER,
                        scope_user_id=request.user.id,
                        status=ContractText.Status.ACTIVE,
                        text=text,
                        updated_by=request.user,
                    )
            else:
                row.text = text
                row.updated_by = request.user
                row.save(update_fields=["text", "updated_by", "updated_at"])
            if project_ctx_id > 0:
                messages.success(request, f"Saved project override for {contract_key}.")
            else:
                messages.success(request, f"Saved user override for {contract_key}.")
            redirect_url = reverse("accounts:system_contracts_dashboard") + f"?key={contract_key}"
            if project_ctx_id > 0:
                redirect_url += f"&project_id={project_ctx_id}"
            return redirect(redirect_url)

        if action == "reset_user" and contract_key in _DASHBOARD_TEXT_KEYS:
            if project_ctx_id > 0:
                updated = ContractText.objects.filter(
                    key=contract_key,
                    scope_type=ContractText.ScopeType.PROJECT,
                    scope_project_id=project_ctx_id,
                    status=ContractText.Status.ACTIVE,
                ).update(
                    status=ContractText.Status.RETIRED,
                    updated_by=request.user,
                )
            else:
                updated = (
                    ContractText.objects.filter(
                        key=contract_key,
                        scope_type=ContractText.ScopeType.USER,
                        status=ContractText.Status.ACTIVE,
                    )
                    .filter(Q(scope_user_id=request.user.id) | Q(scope_id=request.user.id))
                    .update(
                        status=ContractText.Status.RETIRED,
                        updated_by=request.user,
                    )
                )
            if updated:
                if project_ctx_id > 0:
                    messages.success(request, f"Reset project override for {contract_key}.")
                else:
                    messages.success(request, f"Reset user override for {contract_key}.")
            else:
                if project_ctx_id > 0:
                    messages.info(request, f"No project override to reset for {contract_key}.")
                else:
                    messages.info(request, f"No user override to reset for {contract_key}.")
            redirect_url = reverse("accounts:system_contracts_dashboard") + f"?key={contract_key}"
            if project_ctx_id > 0:
                redirect_url += f"&project_id={project_ctx_id}"
            return redirect(redirect_url)

    show_overridden = str(request.GET.get("show_overridden") or "").strip() in {"1", "true", "on", "yes"}
    rows = []
    ordered_sections = OrderedDict(
        [
            ("Core Contracts", list(_DASHBOARD_TEXT_KEYS) + list(_DASHBOARD_DERIVED_KEYS)),
            ("Verification & Approval", list(_DASHBOARD_VERIFICATION_KEYS)),
        ]
    )

    for section_label, section_keys in ordered_sections.items():
        for key in section_keys:
            if key == "cde.contract":
                if not str(get_raw_contract_text(ctx, "cde.contract.0") or "").strip():
                    continue

            usage = _USAGE_HINTS_BY_KEY.get(key, {})

            if key == "boundary.profile":
                effective_profile = _effective_boundary_profile(ctx)
                default_profile = dict(getattr(getattr(ctx, "project", None), "boundary_profile_json", {}) or {})
                user_profile = {}
                if getattr(ctx, "work_item", None) is not None:
                    user_profile = dict(getattr(ctx.work_item, "boundary_profile_json", {}) or {})
                has_user_override = bool(user_profile)
                if show_overridden and not has_user_override:
                    continue
                rows.append(
                    {
                        "section": section_label,
                        "key": key,
                        "label": CONTRACT_TEXT_LABELS.get(key, key),
                        "is_read_only": False,
                        "is_json": True,
                        "default_text": json.dumps(default_profile, ensure_ascii=True, indent=2),
                        "user_text": json.dumps(user_profile, ensure_ascii=True, indent=2) if user_profile else "",
                        "effective_text": json.dumps(effective_profile, ensure_ascii=True, indent=2),
                        "effective_source": "USER" if has_user_override else "DEFAULT",
                        "resolved_from": _resolved_from(ctx, key),
                        "used_in": str(usage.get("used_in") or ""),
                        "flows": str(usage.get("flows") or ""),
                        "has_user_override": has_user_override,
                        "preview_default_prompt": _PREVIEW_DEFAULT_PROMPT,
                    }
                )
                continue

            if key in _DASHBOARD_TEXT_KEYS:
                fallback = _raw_contract_default(ctx, key)
                resolved = resolve_contract_text(
                    request.user,
                    key,
                    project_id=project_ctx_id if project_ctx_id > 0 else None,
                )
                default_text = str(resolved.get("default_text") or "") or fallback
                effective_source = str(resolved.get("effective_source") or "DEFAULT")
                effective_text = str(resolved.get("effective_text") or "")
                if effective_source == "DEFAULT" and not effective_text:
                    effective_text = default_text
                if project_ctx_id > 0:
                    editable_text = resolved.get("project_text")
                else:
                    editable_text = resolved.get("user_text")
                has_user_override = editable_text is not None
                if show_overridden and not has_user_override:
                    continue
                preset_axis, active_preset_name, preset_options = _preset_options_for_key(key, active_level4)
                rows.append(
                    {
                        "section": section_label,
                        "key": key,
                        "label": CONTRACT_TEXT_LABELS.get(key, key),
                        "is_read_only": False,
                        "is_json": False,
                        "default_text": default_text,
                        "user_text": str(editable_text or "") if has_user_override else "",
                        "effective_text": effective_text,
                        "effective_source": effective_source,
                        "editable_scope": "PROJECT" if project_ctx_id > 0 else "USER",
                        "resolved_from": _resolved_from(ctx, key),
                        "used_in": str(usage.get("used_in") or ""),
                        "flows": str(usage.get("flows") or ""),
                        "preset_axis": preset_axis,
                        "active_preset_name": active_preset_name,
                        "preset_options": preset_options,
                        "has_user_override": has_user_override,
                    }
                )
                continue

            raw = _raw_contract_default(ctx, key)
            if not raw and key in _DASHBOARD_VERIFICATION_KEYS:
                continue
            rows.append(
                {
                    "section": section_label,
                    "key": key,
                    "label": CONTRACT_TEXT_LABELS.get(key, key),
                    "is_read_only": True,
                    "is_json": False,
                    "default_text": raw,
                    "user_text": "",
                    "effective_text": raw,
                    "effective_source": "DERIVED",
                    "resolved_from": _resolved_from(ctx, key),
                    "used_in": str(usage.get("used_in") or ""),
                    "flows": str(usage.get("flows") or ""),
                    "has_user_override": False,
                }
            )

    selected_key = str(request.GET.get("key") or "").strip().lower()
    if selected_key not in [row["key"] for row in rows]:
        selected_key = rows[0]["key"] if rows else ""

    selected = None
    for row in rows:
        if row["key"] == selected_key:
            selected = row
            break
    if selected is None and rows:
        selected = rows[0]
        selected_key = selected["key"]

    if str(request.GET.get("ajax") or "").strip() == "1":
        selected_html = render_to_string(
            "accounts/system/_contracts_selected_panel.html",
            {
                "selected": selected,
                "selected_project": selected_project,
            },
            request=request,
        )
        return JsonResponse(
            {
                "ok": True,
                "selected_key": selected_key,
                "selected_html": selected_html,
            }
        )

    return render(
        request,
        "accounts/system/contracts_dashboard.html",
        {
            "rows": rows,
            "selected_key": selected_key,
            "selected": selected,
            "project_options": project_options,
            "selected_project": selected_project,
            "project_ctx_id": project_ctx_id,
            "show_overridden": show_overridden,
            "show_trace": bool(settings.DEBUG),
        },
    )


@login_required
def system_contract_pack_export(request):
    project_id_raw = str(request.GET.get("project_id") or request.POST.get("project_id") or "").strip()
    project = None
    if project_id_raw.isdigit():
        project = accessible_projects_qs(request.user).filter(id=int(project_id_raw)).first()
    if project is not None:
        selected_keys_raw = []
        if request.method == "POST":
            selected_keys_raw = list(request.POST.getlist("keys") or [])
            if not selected_keys_raw:
                messages.error(request, "Select at least one contract to export.")
                return redirect(reverse("accounts:system_contracts_dashboard") + f"?project_id={project.id}")
        else:
            selected_keys_raw = list(request.GET.getlist("keys") or [])
        selected_keys = [str(k or "").strip().lower() for k in selected_keys_raw if str(k or "").strip()]
        selected_set = set(k for k in selected_keys if k in _USER_OVERRIDE_KEYS)

        if selected_keys_raw and not selected_set:
            messages.error(request, "Select at least one valid contract key to export.")
            return redirect(reverse("accounts:system_contracts_dashboard") + f"?project_id={project.id}")

        entries = []
        rows_qs = (
            ContractText.objects.filter(
                scope_type=ContractText.ScopeType.PROJECT,
                scope_project=project,
                status=ContractText.Status.ACTIVE,
                key__in=list(_USER_OVERRIDE_KEYS),
            )
            .order_by("key", "-updated_at")
        )
        if selected_set:
            rows_qs = rows_qs.filter(key__in=list(selected_set))

        seen = set()
        for row in rows_qs:
            if row.key in seen:
                continue
            seen.add(row.key)
            entries.append({"key": row.key, "text": row.text})

        payload = {
            "pack_type": "ProjectContractPack",
            "version": 1,
            "exported_at": timezone.now().isoformat(),
            "project_id": project.id,
            "project_name": str(project.name or ""),
            "selected_keys": sorted(list(selected_set)) if selected_set else [],
            "contracts": entries,
        }
        body = json.dumps(payload, indent=2, ensure_ascii=True)
        response = HttpResponse(body, content_type="application/json")
        response["Content-Disposition"] = f"attachment; filename=project_contract_pack_{project.id}.json"
        return response

    selected_keys_raw = []
    if request.method == "POST":
        selected_keys_raw = list(request.POST.getlist("keys") or [])
        if not selected_keys_raw:
            messages.error(request, "Select at least one contract to export.")
            return redirect("accounts:system_contracts_dashboard")
    else:
        selected_keys_raw = list(request.GET.getlist("keys") or [])
    selected_keys = [str(k or "").strip().lower() for k in selected_keys_raw if str(k or "").strip()]
    selected_set = set(k for k in selected_keys if k in _USER_OVERRIDE_KEYS)

    if selected_keys_raw and not selected_set:
        messages.error(request, "Select at least one valid contract key to export.")
        return redirect("accounts:system_contracts_dashboard")

    entries = []
    rows_qs = (
        ContractText.objects.filter(
            scope_type=ContractText.ScopeType.USER,
            status=ContractText.Status.ACTIVE,
            key__in=list(_USER_OVERRIDE_KEYS),
        )
        .filter(Q(scope_user_id=request.user.id) | Q(scope_id=request.user.id))
        .order_by("key", "-updated_at")
    )
    if selected_set:
        rows_qs = rows_qs.filter(key__in=list(selected_set))

    rows = rows_qs
    seen = set()
    for row in rows:
        if row.key in seen:
            continue
        seen.add(row.key)
        entries.append({"key": row.key, "text": row.text})

    payload = {
        "pack_type": "ContractPack",
        "version": 1,
        "exported_at": timezone.now().isoformat(),
        "user_id": request.user.id,
        "selected_keys": sorted(list(selected_set)) if selected_set else [],
        "contracts": entries,
    }
    body = json.dumps(payload, indent=2, ensure_ascii=True)
    response = HttpResponse(body, content_type="application/json")
    response["Content-Disposition"] = "attachment; filename=contract_pack.json"
    return response


@login_required
def system_contract_pack_import(request):
    if request.method != "POST":
        return redirect("accounts:system_contracts_dashboard")

    upload = request.FILES.get("contract_pack")
    if upload is None:
        messages.error(request, "Choose a JSON file to import.")
        return redirect("accounts:system_contracts_dashboard")

    try:
        payload = json.loads(upload.read().decode("utf-8"))
    except Exception:
        messages.error(request, "Invalid ContractPack JSON.")
        return redirect("accounts:system_contracts_dashboard")

    contracts = payload.get("contracts") if isinstance(payload, dict) else None
    if not isinstance(contracts, list):
        messages.error(request, "ContractPack missing contracts list.")
        return redirect("accounts:system_contracts_dashboard")

    pack_type = str(payload.get("pack_type") or "ContractPack").strip()
    project_id_raw = str(request.POST.get("project_id") or request.GET.get("project_id") or "").strip()
    project = None
    if project_id_raw.isdigit():
        project = accessible_projects_qs(request.user).filter(id=int(project_id_raw)).first()

    if pack_type == "ProjectContractPack":
        if project is None:
            messages.error(request, "Select a project before importing a project contract pack.")
            return redirect("accounts:system_contracts_dashboard")
        if not (request.user.is_staff or request.user.is_superuser or request.user.id == project.owner_id):
            messages.error(request, "Only the project owner or admin can import project contract packs.")
            return redirect(reverse("accounts:system_contracts_dashboard") + f"?project_id={project.id}")
        imported = 0
        ignored = []
        for item in contracts:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip().lower()
            text = str(item.get("text") or "")
            if key not in _USER_OVERRIDE_KEYS:
                ignored.append(key or "(blank)")
                continue
            row = (
                ContractText.objects.filter(
                    key=key,
                    scope_type=ContractText.ScopeType.PROJECT,
                    scope_project=project,
                    status=ContractText.Status.ACTIVE,
                )
                .order_by("-updated_at", "-id")
                .first()
            )
            if row is None:
                ContractText.objects.create(
                    key=key,
                    scope_type=ContractText.ScopeType.PROJECT,
                    scope_project=project,
                    status=ContractText.Status.ACTIVE,
                    text=text,
                    updated_by=request.user,
                )
            else:
                row.text = text
                row.updated_by = request.user
                row.save(update_fields=["text", "updated_by", "updated_at"])
            imported += 1
        if imported:
            messages.success(request, f"Imported {imported} project overrides.")
        if ignored:
            messages.warning(request, "Ignored unsupported keys: " + ", ".join(sorted(set(ignored))))
        return redirect(reverse("accounts:system_contracts_dashboard") + f"?project_id={project.id}")

    imported = 0
    ignored = []
    for item in contracts:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip().lower()
        text = str(item.get("text") or "")
        if key not in _USER_OVERRIDE_KEYS:
            ignored.append(key or "(blank)")
            continue
        row = (
            ContractText.objects.filter(
                key=key,
                scope_type=ContractText.ScopeType.USER,
                status=ContractText.Status.ACTIVE,
            )
            .filter(Q(scope_user_id=request.user.id) | Q(scope_id=request.user.id))
            .order_by("-updated_at", "-id")
            .first()
        )
        if row is None:
            ContractText.objects.create(
                key=key,
                scope_type=ContractText.ScopeType.USER,
                scope_user_id=request.user.id,
                status=ContractText.Status.ACTIVE,
                text=text,
                updated_by=request.user,
            )
        else:
            row.text = text
            row.updated_by = request.user
            row.save(update_fields=["text", "updated_by", "updated_at"])
        imported += 1

    if imported:
        messages.success(request, f"Imported {imported} overrides.")
    if ignored:
        messages.warning(request, "Ignored unsupported keys: " + ", ".join(sorted(set(ignored))))
    return redirect("accounts:system_contracts_dashboard")


@login_required
def system_contract_override_edit(request, key: str):
    contract_key = str(key or "").strip()
    if not contract_key:
        messages.error(request, "Contract key is required.")
        return redirect("accounts:system_contracts_dashboard")

    row = ContractOverride.objects.filter(
        key=contract_key,
        scope_type=ContractOverride.ScopeType.GLOBAL,
    ).first()

    if request.method == "POST":
        is_enabled = request.POST.get("is_enabled") == "on"
        override_text = str(request.POST.get("override_text") or "")
        if row is None:
            row = ContractOverride.objects.create(
                key=contract_key,
                scope_type=ContractOverride.ScopeType.GLOBAL,
                scope_id=None,
                is_enabled=is_enabled,
                override_text=override_text,
                updated_by=request.user,
            )
        else:
            row.is_enabled = is_enabled
            row.override_text = override_text
            row.updated_by = request.user
            row.save(update_fields=["is_enabled", "override_text", "updated_by", "updated_at"])
        messages.success(request, f"Override saved for {contract_key}.")
        return redirect(
            reverse("accounts:system_contracts_dashboard")
            + "?key="
            + contract_key
            + "&mode=effective"
        )

    existing_text = ""
    is_enabled = True
    if row is not None:
        existing_text = row.override_text or ""
        is_enabled = bool(row.is_enabled)
    return render(
        request,
        "accounts/system/contracts_override_edit.html",
        {
            "contract_key": contract_key,
            "override_text": existing_text,
            "is_enabled": is_enabled,
        },
    )
