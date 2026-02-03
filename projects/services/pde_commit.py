# -*- coding: utf-8 -*-
# projects/services/pde_commit.py
#
# PDE v1 - Commit Project CKO (create DRAFT ProjectCKO + file mirror).
# NOTE: Keep code comments 7-bit ASCII only.

from __future__ import annotations

import os
import re
from typing import Dict

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from projects.models import Project, ProjectPolicy, ProjectDefinitionField, ProjectCKO


def _safe_enum(value: str) -> str:
    return (value or "").strip().upper()


def _fmt_block(title: str, body: str) -> str:
    lines = []
    lines.append("# ------------------------------------------------------------")
    lines.append("# " + title)
    lines.append("# ------------------------------------------------------------")
    if body:
        for raw in (body or "").splitlines():
            lines.append("# " + raw.rstrip())
    else:
        lines.append("# (not set)")
    lines.append("")
    return "\n".join(lines)


def _render_project_cko_text(project: Project, locked_fields: Dict[str, str]) -> str:
    now = timezone.now().date().isoformat()

    def g(k: str) -> str:
        return (locked_fields.get(k) or "").strip()

    lines = []
    lines.append("# ============================================================")
    lines.append("# PROJECT CKO - CANONICAL DEFINITION")
    lines.append(
        "# This file governs the creation, approval, dispute, and retirement of organisational anchor points."
    )
    lines.append("# ============================================================")
    lines.append("# CKO ID: CKO-PROJECT-{0:06d}".format(project.id))
    lines.append("# Project Name: " + (project.name or "").strip())
    lines.append("# Owner: " + (getattr(project.owner, "username", "") or "").strip())
    lines.append("# Date: " + now)
    lines.append("# Status: DRAFT")
    lines.append("")

    lines.append(_fmt_block("CANONICAL SUMMARY (<=10 words)", g("canonical.summary")).rstrip())
    lines.append(
        _fmt_block(
            "IDENTITY (WHAT)",
            "Project Type: {0}\nProject Status: {1}".format(
                g("identity.project_type"), g("identity.project_status")
            ),
        ).rstrip()
    )
    lines.append(
        _fmt_block(
            "INTENT (WHY)",
            "Primary Goal:\n{0}\n\nSuccess / Acceptance Criteria:\n{1}".format(
                g("intent.primary_goal"), g("intent.success_criteria")
            ),
        ).rstrip()
    )
    lines.append(
        _fmt_block(
            "SCOPE (BOUNDARIES)",
            "In-Scope:\n{0}\n\nOut-of-Scope:\n{1}\n\nHard Constraints:\n{2}".format(
                g("scope.in_scope"), g("scope.out_of_scope"), g("scope.hard_constraints")
            ),
        ).rstrip()
    )
    lines.append(
        _fmt_block(
            "AUTHORITY MODEL (TRUTH RESOLUTION)",
            "Primary Authorities:\n{0}\n\nSecondary Authorities:\n{1}\n\nDeviation Rules:\n{2}".format(
                g("authority.primary"), g("authority.secondary"), g("authority.deviation_rules")
            ),
        ).rstrip()
    )
    lines.append(
        _fmt_block(
            "INTERPRETIVE / OPERATING POSTURE (HOW)",
            "Interpretive Rules:\n{0}\n\nEpistemic Constraints:\n{1}\n\nNovelty Rules:\n{2}".format(
                g("posture.interpretive_rules"),
                g("posture.epistemic_constraints"),
                g("posture.novelty_rules"),
            ),
        ).rstrip()
    )
    lines.append(
        _fmt_block(
            "STORAGE & DURABILITY (WHERE TRUTH LIVES)",
            "Artefact Root:\n{0}\n\nCanonical Artefact Types:\n{1}\n\nNon-Authoritative:\n{2}".format(
                g("storage.artefact_root_ref"),
                g("storage.canonical_artefact_types"),
                g("storage.non_authoritative"),
            ),
        ).rstrip()
    )
    lines.append(_fmt_block("CANONICAL CONTEXT (REFERENCE NARRATIVE)", g("context.narrative")).rstrip())
    lines.append(
        _fmt_block(
            "STABILITY DECLARATION",
            "This CKO is:\n- Internally consistent\n- Governed by the stated authority model\n- Safe to use as canonical project truth\n\nAny deviation must be deliberate, versioned, and documented.",
        ).rstrip()
    )
    lines.append("# ============================================================")
    lines.append("# END PROJECT CKO")
    lines.append("# ============================================================")
    lines.append("")
    return "\n".join(lines)


def _require_locked(project: Project, required_keys: list[str]) -> Dict[str, str]:
    qs = ProjectDefinitionField.objects.filter(
        project=project,
        field_key__in=required_keys,
        status=ProjectDefinitionField.Status.PASS_LOCKED,
    ).only("field_key", "value_text")

    found: Dict[str, str] = {}
    for row in qs:
        k = (row.field_key or "").strip()
        v = (row.value_text or "").strip()
        if k:
            found[k] = v

    missing = [k for k in required_keys if not (found.get(k) or "").strip()]
    if missing:
        raise ValueError("Cannot commit PDE; missing locked fields: " + ", ".join(missing))
    return found


_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9_\-\/]+$")


def _safe_artefact_root_ref(value: str, project_id: int) -> str:
    """
    Sanitise a user-provided artefact root ref.
    Returns a safe relative path under MEDIA_ROOT, or a stable default.
    """
    ref = (value or "").strip()

    if (not ref) or (not _SAFE_REF_RE.match(ref)):
        return "projects/%d" % project_id

    ref = ref.lstrip("/").replace("..", "").strip("/")
    if not ref:
        return "projects/%d" % project_id

    return ref


def commit_project_definition(
    *,
    project: Project,
    required_keys: list[str],
) -> Dict[str, str]:
    """
    Preconditions:
    - required_keys are PASS_LOCKED in ProjectDefinitionField.

    Effects:
    - Writes a Project CKO file under a safe artefact_root_ref (file mirror).
    - Creates a DRAFT ProjectCKO row (DB authoritative; file is a mirror).
    - Mirrors selected values onto Project and ProjectPolicy.
    - Does NOT mark the project as defined. Definition happens on explicit accept.
    """
    with transaction.atomic():
        locked = _require_locked(project, required_keys)

        requested_root = (locked.get("storage.artefact_root_ref") or "").strip()
        current_root = (project.artefact_root_ref or "").strip()
        chosen_root = _safe_artefact_root_ref(requested_root or current_root, project.id)

        if (project.artefact_root_ref or "").strip() != chosen_root:
            project.artefact_root_ref = chosen_root

        full_root = os.path.join(settings.MEDIA_ROOT, chosen_root)
        os.makedirs(full_root, exist_ok=True)

        filename = "CKO-PROJECT-{0:06d}.md".format(project.id)
        rel_path = os.path.join(chosen_root, filename).replace("\\", "/")
        full_path = os.path.join(settings.MEDIA_ROOT, rel_path)

        text = _render_project_cko_text(project, locked)
        with open(full_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

        ptype = _safe_enum(locked.get("identity.project_type", ""))
        pstatus = _safe_enum(locked.get("identity.project_status", ""))

        if ptype in {c for (c, _) in Project.PrimaryType.choices}:
            project.primary_type = ptype

        if pstatus in {c for (c, _) in Project.Status.choices}:
            project.status = pstatus

        project.purpose = (locked.get("intent.primary_goal") or project.purpose or "").strip()

        last_version = (
            ProjectCKO.objects.filter(project=project).aggregate(Max("version")).get("version__max") or 0
        )
        new_version = last_version + 1

        cko = ProjectCKO.objects.create(
            project=project,
            version=new_version,
            status=ProjectCKO.Status.DRAFT,
            rel_path=rel_path,
            content_text=text,
            field_snapshot=dict(locked),
            created_by=project.owner,
        )

        project.save(
            update_fields=[
                "primary_type",
                "status",
                "purpose",
                "artefact_root_ref",
                "updated_at",
            ]
        )

        ProjectPolicy.objects.get_or_create(project=project)

        return {
            "cko_rel_path": rel_path,
            "project_id": str(project.id),
            "cko_id": str(cko.id),
            "cko_version": str(cko.version),
            "cko_status": cko.status,
        }
