# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from projects.phase_contracts import PHASE_CONTRACTS


@dataclass(frozen=True)
class PhaseResolution:
    content: str
    source: str
    effective_phase_contract: str


def _render_ppde_contract_text(contract) -> str:
    if contract is None:
        return ""
    key = str(getattr(contract, "key", "") or "").strip()
    version = getattr(contract, "version", None)
    title = str(getattr(contract, "title", "") or key).strip()
    lines = [
        "PHASE CONTRACT",
        f"Source: PPDE {title} ({key} v{version})",
        "Purpose:",
        str(getattr(contract, "purpose_text", "") or "").strip(),
        "Inputs:",
        str(getattr(contract, "inputs_text", "") or "").strip(),
        "Outputs:",
        str(getattr(contract, "outputs_text", "") or "").strip(),
        "Method guidance:",
        str(getattr(contract, "method_guidance_text", "") or "").strip(),
        "Acceptance test:",
        str(getattr(contract, "acceptance_test_text", "") or "").strip(),
        "LLM review prompt:",
        str(getattr(contract, "llm_review_prompt_text", "") or "").strip(),
    ]
    return "\n".join(lines).strip()


def _render_work_item_phase_contract(work_item: Any, *, user_text: str = "") -> tuple[str, str]:
    phase = str(getattr(work_item, "active_phase", "") or "").strip().upper()
    contract = PHASE_CONTRACTS.get(phase)
    if not contract:
        return "", ""

    title_value = str(getattr(work_item, "title", "") or "").strip() or f"WorkItem {getattr(work_item, 'id', '')}".strip()
    active_seed = ""
    try:
        log = list(getattr(work_item, "seed_log", []) or [])
        active_revision = int(getattr(work_item, "active_seed_revision", 0) or 0)
        if active_revision > 0:
            for item in log:
                if isinstance(item, dict) and int(item.get("revision") or 0) == active_revision:
                    active_seed = str(item.get("seed_text") or "").strip()
                    break
    except Exception:
        active_seed = ""

    lines = [
        "PHASE CONTRACT",
        f"Source: WorkItem phase {phase}",
        f"WorkItem title: {title_value}",
        f"Active phase: {phase}",
        "Current active seed:",
        active_seed or "(none)",
        f"Role: {str(contract.get('role') or '').strip()}",
        f"Phase goal: {str(contract.get('phase_goal') or '').strip()}",
        "Boundary:",
    ]
    for item in list(contract.get("boundary") or []):
        lines.append("- " + str(item))
    lines.append("Method:")
    for item in list(contract.get("method") or []):
        lines.append("- " + str(item))
    lines.extend(
        [
        "Output requirements:",
        ]
    )
    for header in list(contract.get("output_requirements") or []):
        lines.append("- " + str(header))
    lines.append("Forbidden behaviour:")
    for item in list(contract.get("forbidden_behaviour") or []):
        lines.append("- " + str(item))

    requested = str(user_text or "").upper()
    for phase_name in PHASE_CONTRACTS.keys():
        if phase_name in requested and hasattr(work_item, "evaluate_phase_transition"):
            ok, reason = work_item.evaluate_phase_transition(phase_name)
            if not ok:
                lines.append("Phase gate warning:")
                lines.append(f"- Requested phase '{phase_name}' is blocked: {reason}")
            break

    return "\n".join(lines).strip(), f"workitem:{phase}"


def resolve_phase_contract(ctx: Any) -> PhaseResolution | None:
    if bool(getattr(ctx, "is_ppde", False)):
        contract = getattr(ctx, "ppde_phase_contract", None)
        text = _render_ppde_contract_text(contract)
        if text:
            key = str(getattr(contract, "key", "") or "ppde")
            version = str(getattr(contract, "version", "") or "")
            return PhaseResolution(content=text, source="ppde", effective_phase_contract=f"{key}:v{version}")

    work_item = getattr(ctx, "work_item", None)
    if work_item is not None:
        text, key = _render_work_item_phase_contract(work_item, user_text=str(getattr(ctx, "user_text", "") or ""))
        if text:
            return PhaseResolution(content=text, source="workitem", effective_phase_contract=key)
    return None
