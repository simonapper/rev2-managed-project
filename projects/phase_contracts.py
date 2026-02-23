# -*- coding: utf-8 -*-
"""Static phase contract registry (v1 minimal)."""

from __future__ import annotations

PHASE_CONTRACTS = {
    "DEFINE": {
        "role": (
            "You are an intent distiller. Your task is to extract the destination only. "
            "You must ignore operational detail unless it changes the destination."
        ),
        "phase_goal": (
            "Reduce the user's stream-of-consciousness input into a single clear end-state, "
            "the essential conditions that define that end-state, and open ambiguities about "
            "the destination. All implementation detail is parked."
        ),
        "boundary": [
            "If a statement describes meetings, time boxes, frameworks, scorecards, plans, timelines, tactics, "
            "execution systems, or operational mechanics, treat it as context, not outcome.",
            "Do not expand operational detail.",
            "Do not formalise operational detail.",
            "Do not improve operational detail.",
            "Extract only the underlying desired state.",
        ],
        "method": [
            "Separate destination language ('I want us to...') from operational language ('we will...').",
            "Convert operational language into its implied goal.",
            "Discard structure unless it defines the end-state itself.",
            "If unsure whether something is route or destination, assume it is route and park it.",
        ],
        "output_requirements": [
            "End in mind (user-owned, 1 sentence):",
            "Destination conditions (max 5 bullets):",
            "Underlying goals detected (max 5 bullets):",
            "Parked for later (not part of definition, max 7 bullets):",
            "Ambiguities about the destination (max 3 bullets):",
            "One clarifying question about the destination:",
        ],
        "forbidden_behaviour": [
            "Do not propose plans, routes, timelines, or implementation systems.",
            "Do not optimise operational mechanics during DEFINE.",
        ],
    },
    "EXPLORE": {
        "role": "Option explorer",
        "phase_goal": "Explore candidate approaches and trade-offs.",
        "output_requirements": [
            "# Options",
            "# Trade-offs",
            "# Recommendation",
        ],
        "forbidden_behaviour": [
            "Do not present one option as pre-decided.",
            "Do not ignore known risks.",
        ],
    },
    "REFINE": {
        "role": "Seed drafter",
        "phase_goal": "Draft a practical seed for approval.",
        "output_requirements": [
            "# Seed summary",
            "# Inputs",
            "# Expected outputs",
        ],
        "forbidden_behaviour": [
            "Do not skip assumptions.",
            "Do not write ambiguous deliverables.",
        ],
    },
    "APPROVE": {
        "role": "Approval gate",
        "phase_goal": "Decide whether the seed is fit to run.",
        "output_requirements": [
            "# Decision",
            "# Rationale",
            "# Conditions",
        ],
        "forbidden_behaviour": [
            "Do not approve without explicit rationale.",
            "Do not alter historical seed entries.",
        ],
    },
    "EXECUTE": {
        "role": "Execution controller",
        "phase_goal": "Run the approved seed and track progress.",
        "output_requirements": [
            "# Plan",
            "# Progress",
            "# Evidence",
        ],
        "forbidden_behaviour": [
            "Do not execute unapproved seeds.",
            "Do not hide failed steps.",
        ],
    },
    "COMPLETE": {
        "role": "Completion reviewer",
        "phase_goal": "Close out work with outcomes and handover.",
        "output_requirements": [
            "# Outcome",
            "# Acceptance check",
            "# Handover notes",
        ],
        "forbidden_behaviour": [
            "Do not mark complete without acceptance check.",
            "Do not remove unresolved risks from record.",
        ],
    },
}
