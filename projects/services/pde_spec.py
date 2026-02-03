# -*- coding: utf-8 -*-
# projects/services/pde_spec.py
# NOTE: Keep code comments 7-bit ASCII only.

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from projects.services.pde_rubrics import PDE_FIELD_HELP


@dataclass(frozen=True)
class PDEFieldSpec:
    key: str
    label: str
    tier: str
    required: bool = True
    summary: str = ""
    help_text: str = ""


def _help(key: str) -> dict:
    x = PDE_FIELD_HELP.get(key) or {}
    return {
        "summary": (x.get("summary") or "").strip(),
        "help_text": (x.get("help_text") or "").strip(),
    }


PDE_REQUIRED_FIELDS: List[PDEFieldSpec] = [
    PDEFieldSpec(
        key="canonical.summary",
        label="Canonical summary",
        tier="L1-MUST",
        required=True,
        **_help("canonical.summary"),
    ),
    PDEFieldSpec(
        key="identity.project_type",
        label="Project type",
        tier="L1-MUST",
        required=True,
        **_help("identity.project_type"),
    ),
    PDEFieldSpec(
        key="identity.project_status",
        label="Project status",
        tier="L1-MUST",
        required=True,
        **_help("identity.project_status"),
    ),
    PDEFieldSpec(
        key="intent.primary_goal",
        label="Primary goal",
        tier="L1-MUST",
        required=True,
        **_help("intent.primary_goal"),
    ),
    PDEFieldSpec(
        key="intent.success_criteria",
        label="Success criteria",
        tier="L1-MUST",
        required=True,
        **_help("intent.success_criteria"),
    ),
    PDEFieldSpec(
        key="scope.in_scope",
        label="In-scope",
        tier="L1-MUST",
        required=True,
        **_help("scope.in_scope"),
    ),
    PDEFieldSpec(
        key="scope.out_of_scope",
        label="Out-of-scope",
        tier="L1-MUST",
        required=True,
        **_help("scope.out_of_scope"),
    ),
    PDEFieldSpec(
        key="scope.hard_constraints",
        label="Hard constraints",
        tier="L1-GOOD",
        required=True,
        **_help("scope.hard_constraints"),
    ),
    PDEFieldSpec(
        key="authority.primary",
        label="Primary authorities",
        tier="L1-MUST",
        required=True,
        **_help("authority.primary"),
    ),
    PDEFieldSpec(
        key="authority.secondary",
        label="Secondary authorities",
        tier="L1-GOOD",
        required=True,
        **_help("authority.secondary"),
    ),
    PDEFieldSpec(
        key="authority.deviation_rules",
        label="Conflict handling rules",
        tier="L1-GOOD",
        required=True,
        **_help("authority.deviation_rules"),
    ),
    PDEFieldSpec(
        key="posture.epistemic_constraints",
        label="Assumptions and uncertainties",
        tier="L1-GOOD",
        required=True,
        **_help("posture.epistemic_constraints"),
    ),
    PDEFieldSpec(
        key="posture.novelty_rules",
        label="Innovation rules",
        tier="L1-NICE",
        required=True,
        **_help("posture.novelty_rules"),
    ),
    PDEFieldSpec(
        key="storage.artefact_root_ref",
        label="Artefact root reference",
        tier="L1-MUST",
        required=True,
        **_help("storage.artefact_root_ref"),
    ),
    PDEFieldSpec(
        key="context.narrative",
        label="Context narrative",
        tier="L1-MUST",
        required=True,
        **_help("context.narrative"),
    ),
]
