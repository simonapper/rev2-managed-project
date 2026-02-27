# -*- coding: utf-8 -*-

from __future__ import annotations


DERAX_PHASES = ["DEFINE", "EXPLORE", "REFINE", "APPROVE", "EXECUTE"]

PHASE_MANIFEST = {
    "DEFINE": {
        "title": "Clarify Destination",
        "goal": "Identify the core destination and expose ambiguity.",
        "required_paths": [
            "intent.destination",
        ],
        "target_paths": [
            "intent.destination",
            "intent.open_questions",
            "intent.assumptions",
            "parked_for_later.items",
        ],
        "forbidden_prefixes": [
            "explore.",
            "artefacts.",
        ],
        "guidance": [
            "Produce ONE provisional destination sentence.",
            "Ask 1-3 high-leverage clarification questions.",
            "Always include at least one subtext question (what problem this solves, why now, or what changes if it succeeds).",
            "If clearly implied, include at most ONE hypothesis in intent.assumptions prefixed with 'HYPOTHESIS:'.",
            "Do NOT generate success criteria.",
            "Do NOT design frameworks, artefacts, scorecards, or plans.",
            "Do NOT merge, optimise, or expand.",
            "Maximum 3 parked items.",
            "Keep everything concise.",
            "Realism rule: respect constraints but do not elaborate.",
            "Precedence rule: earlier high-level intent defines scope.",
        ],
        "caps": {
            "intent.open_questions": 3,
            "parked_for_later.items": 3,
            "intent.assumptions": 1,
            "intent.success_criteria": 0,
            "artefacts.proposed": 0,
            "canonical_summary_words": 10,
        },
    },
    "EXPLORE": {
        "title": "Exploration and Realism Stress Test",
        "goal": "Widen the lens and test whether the defined destination is complete, realistic, and properly scoped.",
        "required_paths": [
            "intent.destination",
            "explore.adjacent_ideas",
            "explore.risks",
            "explore.tradeoffs",
            "explore.reframes",
        ],
        "target_paths": [
            "explore.adjacent_ideas",
            "explore.risks",
            "explore.tradeoffs",
            "explore.reframes",
            "intent.open_questions",
            "parked_for_later.items",
        ],
        "forbidden_prefixes": [
            "artefacts.",
        ],
        "guidance": [
            "Restate the current destination briefly before analysis.",
            "Identify adjacent strategic angles that may alter the destination.",
            "Surface hidden assumptions and implicit trade-offs.",
            "Apply realism stress-testing and constructive pushback.",
            "Convert plan ideas into risks, trade-offs, or scope questions.",
            "Do not design route, plan, or implementation sequence.",
            "Realism rule: Respect explicitly stated time and capacity constraints. Output alignment-level agreements, not mechanism-level design when context is limited.",
            "Precedence rule: Earlier high-level intent defines scope. Later detail must not override or intensify it.",
            "Contention rule: If later content conflicts with earlier intent, preserve the earlier intent and record the conflict as an open question (intent.open_questions) rather than resolving it silently.",
            "Do not invent additional specificity to resolve ambiguity unless explicitly instructed.",
        ],
    },
    "REFINE": {
        "title": "Destination Pack Synthesis",
        "goal": "Merge prior DERAX outputs into a stable destination pack.",
        "required_paths": [
            "intent.destination",
            "intent.success_criteria",
            "intent.constraints",
            "intent.non_goals",
            "explore.risks",
            "explore.tradeoffs",
        ],
        "target_paths": [
            "canonical_summary",
            "intent.destination",
            "intent.success_criteria",
            "intent.constraints",
            "intent.non_goals",
            "intent.assumptions",
            "intent.open_questions",
            "explore.risks",
            "explore.tradeoffs",
            "explore.reframes",
            "parked_for_later.items",
        ],
        "forbidden_prefixes": [
            "artefacts.",
        ],
        "guidance": [
            "Merge overlapping points into one best phrased point.",
            "Hierarchy rule: Earlier high-level statements define scope. Later detail must not override or expand them.",
            "Realism rule: Respect stated time/capacity constraints. For short sessions, output alignment-level agreements, not mechanism-level design.",
            "De-duplicate and prioritise decision-relevant items first.",
            "Compression rule: Prefer simplification over elaboration when merging. If unsure, choose the simpler phrasing.",
            "Detail handling: Convert mechanism detail into either (a) a principle-level condition, or (b) park it in parked_for_later.items.",
            "Trim lists to field limits and move overflow to parked_for_later.items.",
            "No route/plan/timeline: Any plan-like content goes to parked_for_later.items.",
            "If trimming needs user choice, include one forced-choice question in intent.open_questions.",
            "Return only canonical schema keys and keep non-target sections empty.",
            "Realism rule: Respect explicitly stated time and capacity constraints. Output alignment-level agreements, not mechanism-level design when context is limited.",
            "Precedence rule: Earlier high-level intent defines scope. Later detail must not override or intensify it.",
            "Contention rule: If later content conflicts with earlier intent, preserve the earlier intent and record the conflict as an open question (intent.open_questions) rather than resolving it silently.",
            "Do not invent additional specificity to resolve ambiguity unless explicitly instructed.",
        ],
    },
    "APPROVE": {
        "title": "Stability Approval",
        "goal": "Test destination stability and produce approval-ready output without route design.",
        "required_paths": [
            "canonical_summary",
            "intent.destination",
            "intent.success_criteria",
            "intent.constraints",
            "intent.non_goals",
            "intent.assumptions",
            "intent.open_questions",
            "explore.risks",
            "explore.tradeoffs",
            "explore.reframes",
        ],
        "target_paths": [
            "canonical_summary",
            "intent.destination",
            "intent.success_criteria",
            "intent.constraints",
            "intent.non_goals",
            "intent.assumptions",
            "intent.open_questions",
            "explore.risks",
            "explore.tradeoffs",
            "explore.reframes",
            "parked_for_later.items",
        ],
        "forbidden_prefixes": [
            "artefacts.",
        ],
        "guidance": [
            "Stress-test the refined destination for contradictions and weak assumptions.",
            "Keep output decision-level and avoid route or execution design.",
            "Record unresolved conflicts in intent.open_questions.",
            "Behaviour: flag warnings and blocking errors. Do not auto-block; record findings for human judgement.",
            "Realism rule: Respect explicitly stated time and capacity constraints. Output alignment-level agreements, not mechanism-level design when context is limited.",
            "Precedence rule: Earlier high-level intent defines scope. Later detail must not override or intensify it.",
            "Contention rule: If later content conflicts with earlier intent, preserve the earlier intent and record the conflict as an open question (intent.open_questions) rather than resolving it silently.",
            "Do not invent additional specificity to resolve ambiguity unless explicitly instructed.",
        ],
    },
    "EXECUTE": {
        "title": "Facilitation Pack Builder",
        "goal": "Turn the approved destination into practical artefacts to run it.",
        "required_paths": [
            "artefacts.proposed",
        ],
        "target_paths": [
            "artefacts.proposed",
            "parked_for_later.items",
            "intent.open_questions",
        ],
        "forbidden_prefixes": [],
        "guidance": [
            "Produce enabling artefacts, not the work itself.",
            "Respect stated time and capacity constraints; keep outputs lightweight.",
            "Use placeholders for unknowns; do not invent facts.",
            "If too much, prioritise top outcomes and park overflow.",
            "For meetings: propose a workbook/run-sheet/checklist and optional slides outline.",
            "For lessons: propose a lesson plan and teaching materials outline.",
            "Default behaviour: if user input is empty or non-specific, propose 3 concrete artefacts in artefacts.proposed using supported kinds with explicit kind/title/notes.",
            "Fallback suggestion: include a short instruction the user can copy to request specific outputs.",
            "Override rule: if the user explicitly requests a different artefact set or count, follow the user request.",
        ],
    },
}


def get_phase_manifest(phase: str) -> dict:
    p = (phase or "").strip().upper()
    if p not in PHASE_MANIFEST:
        raise ValueError(f"Unknown DERAX phase: {phase}")
    return PHASE_MANIFEST[p]


def build_phase_contract_text(phase: str) -> str:
    p = (phase or "").strip().upper()
    m = get_phase_manifest(p)

    lines = []
    lines.append(f"Phase: {p}")
    lines.append(f"Goal: {m['goal']}")
    lines.append("")
    lines.append("Guidance:")
    for g in m["guidance"]:
        lines.append(f"- {g}")
    lines.append("")
    lines.append("Populate these paths:")
    for path in m["target_paths"]:
        lines.append(f"- {path}")
    lines.append("")
    lines.append("Keep these sections empty:")
    for pref in m["forbidden_prefixes"]:
        lines.append(f"- {pref}")
    lines.append("")
    lines.append("Return ONLY a single JSON object. No markdown. No commentary.")
    return "\n".join(lines)
