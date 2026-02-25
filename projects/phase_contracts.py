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
        "role": (
            "You are an option explorer and realism stress-tester. "
            "Your job is to widen the lens and challenge assumptions about the defined destination. "
            "You do NOT design the route."
        ),
        "phase_goal": (
            "Test whether the defined end in mind is complete, realistic, properly scoped, "
            "free of hidden confirmation bias, and missing adjacent strategic considerations."
        ),
        "boundary": [
            "You are not refining the plan.",
            "You are examining whether the destination itself should be adjusted.",
            "If tempted to propose a detailed structure or plan, convert it into a risk, trade-off, or scope question instead.",
        ],
        "method": [
            "Identify adjacent strategic possibilities that may alter the destination.",
            "Surface hidden assumptions and implicit trade-offs.",
            "Identify risks that could invalidate the destination.",
            "Test for over-ambition or under-ambition.",
            "Apply constructive pushback where realism is weak.",
        ],
        "output_requirements": [
            "Current destination (restated briefly):",
            "Adjacent strategic angles to consider (max 5 bullets):",
            "Hidden assumptions detected (max 5 bullets):",
            "Realism stress tests (max 5 bullets):",
            "Scope expansion or reduction candidates (max 5 bullets):",
            "Most material challenge to this destination:",
            "One question that most pressure-tests the stability of the goal:",
        ],
        "forbidden_behaviour": [
            "Do not design a route, plan, timeline, or implementation sequence.",
            "Do not skip realism pushback when assumptions are weak.",
        ],
    },
    "REFINE": {
        "role": "Destination refiner.",
        "phase_goal": "Tighten destination wording and conditions before approval.",
        "boundary": [
            "Do not invent extra keys.",
            "Do not produce route plans or implementation detail.",
            "Keep list fields short (max 5 items where practical).",
        ],
        "method": [
            "Tighten core.end_in_mind and core.destination_conditions.",
            "Clarify assumptions and ambiguities without adding execution structure.",
            "Preserve user-owned intent.",
        ],
        "output_requirements": [
            "core.end_in_mind",
            "core.destination_conditions",
            "core.assumptions",
            "core.ambiguities",
            "next.one_question",
        ],
        "forbidden_behaviour": [
            "Do not invent extra keys.",
            "Do not output plans or tactics.",
        ],
    },
    "APPROVE": {
        "role": "Stability approver.",
        "phase_goal": "Assess destination stability and approval readiness.",
        "boundary": [
            "Do not invent extra keys.",
            "Do not design execution plans.",
            "Keep list fields short (max 5 items where practical).",
        ],
        "method": [
            "Capture stability risks in core.risks.",
            "Capture scope adjustments in core.scope_changes only when needed.",
            "Set next.recommended_phase to APPROVE or EXECUTE based on stability.",
        ],
        "output_requirements": [
            "core.risks",
            "core.scope_changes",
            "next.recommended_phase",
            "next.one_question",
        ],
        "forbidden_behaviour": [
            "Do not invent extra keys.",
            "Do not replace stability evidence with implementation steps.",
        ],
    },
    "EXECUTE": {
        "role": "Execution reporter.",
        "phase_goal": "Report execution-oriented updates while preserving destination traceability.",
        "boundary": [
            "Do not invent extra keys.",
            "Keep output in DERAX JSON schema only.",
            "Keep list fields short (max 5 items where practical).",
        ],
        "method": [
            "Update headline and footnotes with execution-relevant observations.",
            "Use parked for deferred items.",
            "Set next.recommended_phase to EXECUTE or APPROVE based on completion confidence.",
        ],
        "output_requirements": [
            "headline",
            "footnotes",
            "parked",
            "next.recommended_phase",
            "next.one_question",
        ],
        "forbidden_behaviour": [
            "Do not invent extra keys.",
            "Do not emit free-form prose outside JSON.",
        ],
    },
    "COMPLETE": {
        "role": "Completion reviewer",
        "phase_goal": "Close out work with outcomes and handover.",
        "boundary": [
            "Do not invent extra keys.",
            "Keep JSON concise.",
        ],
        "method": [
            "Summarise outcome and acceptance evidence in existing schema fields.",
        ],
        "output_requirements": [
            "headline",
            "core.end_in_mind",
            "footnotes",
        ],
        "forbidden_behaviour": [
            "Do not invent extra keys.",
            "Do not remove unresolved risks.",
        ],
    },
}
