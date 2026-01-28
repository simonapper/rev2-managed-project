# -*- coding: utf-8 -*-
# chats/services/cde_rubrics.py
#
# CDE v1 - Field rubrics for chat definition.
# NOTE: Keep code comments 7-bit ASCII only.

from __future__ import annotations

from typing import Dict


CDE_FIELD_RUBRICS: Dict[str, str] = {
    "chat.goal": (
        "Aim: one sentence describing the single primary outcome of this chat.\n"
        "PASS if:\n"
        "- States a concrete objective (not just a topic).\n"
        "- Is narrow enough to complete in this chat.\n"
        "- Avoids vague verbs like 'discuss', 'explore' unless paired with a deliverable.\n"
        "WEAK if:\n"
        "- Too broad (multiple goals) or only a general topic.\n"
        "- No deliverable implied.\n"
        "CONFLICT if:\n"
        "- Contradicts another locked chat field (constraints/non-goals/success).\n"
    ),
    "chat.success": (
        "Aim: define how we will know the chat achieved the goal.\n"
        "PASS if:\n"
        "- Provides an observable completion test (deliverable or decision).\n"
        "- Is measurable or checkable (e.g. 'draft email', 'list 10 options', 'finalise 3 rules').\n"
        "- Matches the chat.goal.\n"
        "WEAK if:\n"
        "- Uses fuzzy outcomes (e.g. 'feel clearer', 'understand better') without a check.\n"
        "- Adds scope beyond chat.goal.\n"
        "CONFLICT if:\n"
        "- Success implies work excluded by non-goals/constraints.\n"
    ),
    "chat.constraints": (
        "Aim: hard boundaries that must be respected.\n"
        "PASS if:\n"
        "- Lists up to 3 hard constraints, or 'none'.\n"
        "- Constraints are actionable (time, format, tools, tone, assumptions, sources).\n"
        "- Does not restate non-goals (use non-goals for scope exclusions).\n"
        "WEAK if:\n"
        "- Too many items, or items are preferences not constraints.\n"
        "- Constraints are ambiguous (e.g. 'be brief' with no threshold).\n"
        "CONFLICT if:\n"
        "- Conflicts with chat.goal or makes success impossible.\n"
    ),
    "chat.non_goals": (
        "Aim: explicit exclusions to prevent scope creep.\n"
        "PASS if:\n"
        "- Lists up to 3 exclusions, or 'none'.\n"
        "- Clearly states what we will NOT do.\n"
        "- Does not contradict chat.goal.\n"
        "WEAK if:\n"
        "- Uses vague exclusions (e.g. 'nothing too detailed') without clarity.\n"
        "- Exclusions overlap heavily with constraints.\n"
        "CONFLICT if:\n"
        "- Excludes the main work required to satisfy chat.goal/success.\n"
    ),
}
