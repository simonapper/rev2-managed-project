# -*- coding: utf-8 -*-
# projects/services/pde_loop.py
#
# PDE v1 - Run the project definition flow (draft/controlled) and persist to ProjectDefinitionField.
# NOTE: Keep code comments 7-bit ASCII only.

from __future__ import annotations

from typing import Any, Dict, List, Optional

from django.utils import timezone

from projects.models import Project, ProjectDefinitionField
from projects.services.pde import validate_field
from projects.services.pde_spec import PDE_REQUIRED_FIELDS


def _get_or_create_field(project: Project, field_key: str, tier: str) -> ProjectDefinitionField:
    obj, _ = ProjectDefinitionField.objects.get_or_create(
        project=project,
        field_key=field_key,
        defaults={"tier": tier, "status": ProjectDefinitionField.Status.DRAFT},
    )
    # Keep tier in sync (in case spec changes).
    if (obj.tier or "") != (tier or ""):
        obj.tier = tier
        obj.save(update_fields=["tier", "updated_at"])
    return obj


def _validate_one(
    *,
    spec,
    proposed: str,
    locked_fields: Dict[str, str],
    generate_panes_func,
) -> Dict[str, Any]:
    rubric_text = getattr(spec, "help_text", "") or ""
    return validate_field(
        generate_panes_func=generate_panes_func,
        field_key=spec.key,
        value_text=proposed,
        locked_fields=locked_fields,
        rubric=rubric_text,
    )


def ensure_pde_fields(project: Project) -> None:
    for spec in PDE_REQUIRED_FIELDS:
        _get_or_create_field(project, spec.key, spec.tier)


def read_locked_fields(project: Project) -> Dict[str, str]:
    qs = ProjectDefinitionField.objects.filter(
        project=project,
        status=ProjectDefinitionField.Status.PASS_LOCKED,
    ).only("field_key", "value_text")
    out: Dict[str, str] = {}
    for row in qs:
        k = (row.field_key or "").strip()
        v = (row.value_text or "").strip()
        if k and v:
            out[k] = v
    return out


def run_pde_controlled(
    *,
    project: Project,
    user,
    generate_panes_func,
    user_inputs: Dict[str, str],
) -> Dict[str, Any]:
    """
    Controlled PDE:
    - Validate in order.
    - Require PASS for each required field.
    - Persist PASS_LOCKED value_text + last_validation + locked_by/locked_at.

    Returns:
    { ok, locked, results, first_blocker, locked_fields }
    """
    ensure_pde_fields(project)

    results: List[Dict[str, Any]] = []
    first_blocker: Optional[Dict[str, Any]] = None
    locked_fields: Dict[str, str] = read_locked_fields(project)

    for spec in PDE_REQUIRED_FIELDS:
        field_key = spec.key
        proposed = (user_inputs.get(field_key) or "").strip()

        vobj = _validate_one(
            spec=spec,
            proposed=proposed,
            locked_fields=locked_fields,
            generate_panes_func=generate_panes_func,
        )
        results.append(vobj)

        if vobj.get("verdict") != "PASS":
            first_blocker = vobj
            # Persist draft + validation so UI can show feedback.
            row = _get_or_create_field(project, field_key, spec.tier)
            row.status = ProjectDefinitionField.Status.PROPOSED
            row.value_text = proposed
            row.last_validation = vobj
            row.save(update_fields=["status", "value_text", "last_validation", "updated_at"])
            break

        locked_value = (vobj.get("suggested_revision") or proposed).strip()
        locked_fields[field_key] = locked_value

        row = _get_or_create_field(project, field_key, spec.tier)
        row.status = ProjectDefinitionField.Status.PASS_LOCKED
        row.value_text = locked_value
        row.last_validation = vobj
        row.locked_at = timezone.now()
        row.locked_by = user
        row.save(
            update_fields=[
                "status",
                "value_text",
                "last_validation",
                "locked_at",
                "locked_by",
                "updated_at",
            ]
        )

    ok = first_blocker is None and len(results) == len(PDE_REQUIRED_FIELDS)
    return {
        "ok": ok,
        "locked": ok,
        "results": results,
        "first_blocker": first_blocker,
        "locked_fields": locked_fields,
    }


def validate_pde_inputs(
    *,
    project: Project,
    generate_panes_func,
    user_inputs: Dict[str, str],
) -> Dict[str, Any]:
    """
    Validate without persisting lock state (useful for preflight).
    """
    results: List[Dict[str, Any]] = []
    first_blocker: Optional[Dict[str, Any]] = None
    locked_fields: Dict[str, str] = read_locked_fields(project)

    for spec in PDE_REQUIRED_FIELDS:
        field_key = spec.key
        proposed = (user_inputs.get(field_key) or "").strip()

        vobj = _validate_one(
            spec=spec,
            proposed=proposed,
            locked_fields=locked_fields,
            generate_panes_func=generate_panes_func,
        )
        results.append(vobj)

        if vobj.get("verdict") != "PASS":
            first_blocker = vobj
            break

        locked_value = (vobj.get("suggested_revision") or proposed).strip()
        locked_fields[field_key] = locked_value

    ok = first_blocker is None and len(results) == len(PDE_REQUIRED_FIELDS)
    return {
        "ok": ok,
        "locked": ok,
        "results": results,
        "first_blocker": first_blocker,
        "locked_fields": locked_fields,
    }
