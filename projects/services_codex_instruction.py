# -*- coding: utf-8 -*-

from __future__ import annotations

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from projects.models import WorkItem


def _locked_seed_text(work_item: WorkItem) -> str:
    log = list(work_item.seed_log or [])
    for item in log:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "") == WorkItem.SEED_STATUS_PASS_LOCKED:
            return str(item.get("seed_text") or "").strip()
    return ""


def _build_codex_instruction_markdown(work_item: WorkItem, locked_seed: str) -> str:
    lines = [
        "# Goal (locked seed)",
        "",
        locked_seed or "(none)",
        "",
        "# Scope (in / out)",
        "",
        "- In: Implement the locked-seed instruction plan.",
        "- Out: Broad refactors and unrelated behaviour changes.",
        "",
        "# Files to change (placeholders acceptable if unknown)",
        "",
        "- [TBD] Identify target module(s) before editing.",
        "",
        "# Invariants (seed_log append-only, single PASS_LOCKED, etc.)",
        "",
        "- seed_log remains append-only.",
        "- Only one seed revision can be PASS_LOCKED.",
        "- Phase gates must remain enforced centrally.",
        "",
        "# Step-by-step tasks (numbered)",
        "",
        "1. Confirm locked seed and execution scope.",
        "2. Implement minimal code changes required by the seed.",
        "3. Add or update tests for changed behaviour.",
        "4. Run verification checks and record outcomes.",
        "",
        "# Tests (how to verify)",
        "",
        "- Run targeted tests for changed modules.",
        "- Run project checks for migration and model consistency.",
        "",
        "# Don’t-do list (prevent scope creep)",
        "",
        "- Do not mutate existing seed history entries.",
        "- Do not add unrequested features.",
        "- Do not change contracts or validators outside this slice.",
        "",
    ]
    return "\n".join(lines)


def generate_codex_instruction(work_item: WorkItem) -> str:
    if work_item is None:
        raise ValueError("work_item is required.")

    locked_seed = _locked_seed_text(work_item)
    if not locked_seed:
        raise ValueError("Cannot generate CODEX instruction without PASS_LOCKED seed.")

    markdown = _build_codex_instruction_markdown(work_item, locked_seed)
    path = f"projects/{work_item.project_id}/workitems/{work_item.id}/CODEX_INSTRUCTION.md"
    default_storage.save(path, ContentFile(markdown.encode("utf-8")))

    work_item.add_deliverable(path, note="CODEX instruction artefact")

    if str(work_item.active_phase or "").upper() == WorkItem.PHASE_EXECUTE:
        work_item.set_phase(WorkItem.PHASE_COMPLETE)
    else:
        work_item.refresh_from_db()

    return markdown

