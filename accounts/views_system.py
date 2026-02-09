# -*- coding: utf-8 -*-
# accounts/views_system.py

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from config.models import ConfigRecord, ConfigVersion, SystemConfigPointers, ConfigScope
from projects.models import PhaseContract


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
