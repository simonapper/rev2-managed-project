# -*- coding: utf-8 -*-
# projects/services/pde_rubrics.py
# NOTE: Keep code comments 7-bit ASCII only.

from __future__ import annotations

from typing import Dict

PDE_FIELD_HELP: Dict[str, Dict[str, str]] = {
    "intent.success_criteria": {
        "summary": "How we know the project definition is complete.",
        "help_text": (
            "Write observable completion tests.\n"
            "- Prefer deliverables or decisions.\n"
            "- Avoid fuzzy outcomes.\n"
            "- Keep it consistent with the primary goal.\n"
        ),
    },
    "scope.in_scope": {
        "summary": "What this project will cover.",
        "help_text": (
            "List what is included.\n"
            "- Bullet lists are fine.\n"
            "- Keep it at project level, not a task list.\n"
        ),
    },
    "scope.out_of_scope": {
        "summary": "What we explicitly will not do.",
        "help_text": (
            "List exclusions to prevent scope creep.\n"
            "- Keep it concrete.\n"
            "- Do not contradict in-scope.\n"
        ),
    },
    "scope.hard_constraints": {
        "summary": "Non-negotiable boundaries.",
        "help_text": (
            "Constraints that must be respected.\n"
            "- Examples: safety, compliance, time, tools, formats.\n"
            "- If unknown, write DEFERRED.\n"
        ),
    },
    "authority.primary": {
        "summary": "What sources win if there is disagreement.",
        "help_text": (
            "Name the top authority sources.\n"
            "- Example: School policy manual; legal requirements; sponsor intent.\n"
            "- Be explicit about precedence.\n"
        ),
    },
    "authority.secondary": {
        "summary": "Helpful sources that do not override primary authorities.",
        "help_text": (
            "Name secondary references.\n"
            "- Standards, guidelines, stakeholder input.\n"
            "- Use DEFERRED if not decided yet.\n"
        ),
    },
    "authority.deviation_rules": {
        "summary": "What to do when instructions conflict with authorities or constraints.",
        "help_text": (
            "Define conflict behaviour.\n"
            "- Must flag the conflict.\n"
            "- Must explain why.\n"
            "- Must propose a labelled compliant alternative.\n"
            "- Prefer a consistent label scheme.\n"
        ),
    },
    "posture.epistemic_constraints": {
        "summary": "Assumptions, unknowns, and what must be labelled as provisional.",
        "help_text": (
            "List what is not known or is assumed.\n"
            "- Example: duration unknown; device constraints unknown.\n"
            "- Anything provisional should be labelled.\n"
        ),
    },
    "posture.novelty_rules": {
        "summary": "Whether we may introduce new ideas and how experimental to be.",
        "help_text": (
            "Define allowed innovation.\n"
            "- Conservative vs exploratory.\n"
            "- If proposing new methods, label as experimental.\n"
        ),
    },
    "storage.artefact_root_ref": {
        "summary": "Where project artefacts live (logical reference).",
        "help_text": (
            "Use the system-managed project artefact root.\n"
            "- Usually auto-set by bootstrap (projects/<id>/artefacts/).\n"
            "- If you use a logical naming scheme, keep it stable.\n"
        ),
    },
    "context.narrative": {
        "summary": "A short reference description: what, why, who, how, where, when.",
        "help_text": (
            "Write a concise narrative so others can understand the project.\n"
            "- What: what it is.\n"
            "- Why: purpose.\n"
            "- Who: stakeholders.\n"
            "- How: approach/rails.\n"
            "- Where: environment/storage.\n"
            "- When: timing assumptions.\n"
        ),
    },
}
