# -*- coding: utf-8 -*-

from __future__ import annotations

from chats.services.derax.contracts import build_phase_contract_text
from chats.services.derax.schema import DERAX_PHASES


def _normalise_phase(phase: str) -> str:
    value = str(phase or "").strip().upper()
    if value not in DERAX_PHASES:
        return "DEFINE"
    return value


def build_derax_system_envelope(*, phase: str) -> str:
    resolved = _normalise_phase(phase)
    return "\n".join(
        [
            "DERAX JSON ENVELOPE",
            "Return ONLY a single JSON object.",
            "No markdown. No commentary. No text outside JSON.",
            "Keys must match canonical schema exactly; do not omit keys.",
            "Unused fields must be empty strings/arrays/objects.",
            f"Set meta.phase to: {resolved}",
        ]
    ).strip()


def build_phase_contract(*, phase: str) -> str:
    resolved = _normalise_phase(phase)
    return build_phase_contract_text(resolved)


def build_derax_system_blocks(*, base_system_blocks: list[str], phase: str) -> list[str]:
    envelope_block = build_derax_system_envelope(phase=phase)
    phase_block = build_phase_contract(phase=phase)
    tail = [str(block or "").strip() for block in list(base_system_blocks or []) if str(block or "").strip()]
    return [envelope_block, phase_block, *tail]


# Backward-compatible helper used by pipeline.
def build_derax_envelope_block(active_phase: str) -> str:
    phase = str(active_phase or "").strip().upper()
    if phase not in DERAX_PHASES:
        phase = "DEFINE"
    return build_derax_system_envelope(phase=phase)
