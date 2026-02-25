# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any

from chats.models import ContractOverride
from chats.services.cde_injection import build_cde_system_blocks
from chats.services.contracts.boundary_resolver import resolve_boundary_contract
from chats.services.contracts.texts import map_block_key_to_contract_text_key, resolve_contract_text
from chats.services.contracts.phase_resolver import resolve_phase_contract
from projects.phase_contracts import PHASE_CONTRACTS
from projects.services.llm_instructions import build_system_messages


_REVIEW_ROUTE_SEED_TEXT = "\n".join(
    [
        "You are generating only ROUTE stages from an INTENT anchor.",
        "Return pane JSON as normal, but put strict JSON stage-map in output.",
        "In output, return ONLY this object schema:",
        "{",
        '  "stages": [',
        "    {",
        '      "title": "string",',
        '      "purpose": "string",',
        '      "inputs": "string",',
        '      "stage_process": "string",',
        '      "outputs": "string",',
        '      "assumptions": "string",',
        '      "duration_estimate": "string",',
        '      "risks_notes": "string"',
        "    }",
        "  ]",
        "}",
        "Rules: stages count 3 to 8. Concrete steps. Ordered flow. No markdown.",
    ]
)

_REVIEW_INTENT_SEED_TEXT = "\n".join(
    [
        "You are producing an INTENT CKO payload.",
        "Return strict JSON only in output, no markdown.",
        "Fill all required fields with concise content.",
        "If unknown, write DEFERRED rather than leaving empty.",
        "Schema:",
        "{",
        '  "canonical_summary": "string <= 10 words",',
        '  "scope": "string",',
        '  "statement": "string",',
        '  "supporting_basis": "string",',
        '  "assumptions": "string",',
        '  "alternatives_considered": "string",',
        '  "uncertainties_limits": "string",',
        '  "provenance": "string"',
        "}",
    ]
)

_REVIEW_EXECUTE_ACTIONS_TEXT = "\n".join(
    [
        "You synthesise execution actions for each route stage.",
        "Keep stage ids unchanged. Do not invent stage ids.",
        "Return strict JSON only in output, no markdown.",
        "For each stage, write concrete next actions that move from inputs to outputs.",
        "Use short lines. Use '- ' bullets in next_actions.",
        "Keep notes short and practical.",
    ]
)

_ROLLUP_TEXT = "\n".join(
    [
        "Update a rolling summary for future context injection.",
        "Return JSON:",
        "{",
        '  "summary": "bullet points (5-10)",',
        '  "conclusion": "short paragraph"',
        "}",
        "Return JSON only.",
    ]
)

_ENVELOPE_TEXT = (
    "Return JSON with keys:\n"
    "- answer: direct response\n"
    "- key_info: bullets / anchors\n"
    "- visuals: emojis, steps, breadcrumbs, ASCII diagrams\n"
    "- reasoning: reasoning summary\n"
    "- output: extractable artefact text\n"
    "Return strict JSON only. No markdown. No prose outside JSON."
)


def _render_static_phase_contract(phase_name: str) -> str:
    phase = str(phase_name or "").strip().upper()
    contract = PHASE_CONTRACTS.get(phase)
    if not isinstance(contract, dict):
        return ""

    lines = [
        "PHASE CONTRACT",
        f"Source: Phase {phase}",
        f"Active phase: {phase}",
        f"Role: {str(contract.get('role') or '').strip()}",
        f"Phase goal: {str(contract.get('phase_goal') or '').strip()}",
        "Boundary:",
    ]
    for item in list(contract.get("boundary") or []):
        lines.append("- " + str(item))
    lines.append("Method:")
    for item in list(contract.get("method") or []):
        lines.append("- " + str(item))
    lines.append("Output requirements:")
    for header in list(contract.get("output_requirements") or []):
        lines.append("- " + str(header))
    lines.append("Forbidden behaviour:")
    for item in list(contract.get("forbidden_behaviour") or []):
        lines.append("- " + str(item))
    return "\n".join(lines).strip()


def get_raw_contract_text(ctx: Any, key: str) -> str:
    k = str(key or "").strip()
    if not k:
        return ""
    if k == "envelope.json_schema":
        if bool(getattr(ctx, "strict_json", True)):
            return _ENVELOPE_TEXT
        return _ENVELOPE_TEXT.replace(
            "Return strict JSON only. No markdown. No prose outside JSON.", ""
        ).strip()
    if k.startswith("avatars.protocol."):
        try:
            idx = int(k.split(".")[-1])
        except Exception:
            return ""
        blocks = build_system_messages(dict(getattr(ctx, "effective_context", {}) or {}))
        if 0 <= idx < len(blocks):
            return str(blocks[idx] or "").strip()
        return ""
    if k == "boundary.effective":
        out = resolve_boundary_contract(ctx)
        return str(out.content or "").strip() if out else ""
    if k == "phase.contract":
        out = resolve_phase_contract(ctx)
        return str(out.content or "").strip() if out else ""
    if k.startswith("phase.") and k != "phase.contract":
        phase_name = k.split(".", 1)[1].strip().upper()
        return _render_static_phase_contract(phase_name)
    if k.startswith("cde.contract."):
        try:
            idx = int(k.split(".")[-1])
        except Exception:
            return ""
        chat = getattr(ctx, "chat", None)
        if chat is None:
            return ""
        blocks = build_cde_system_blocks(chat)
        if 0 <= idx < len(blocks):
            return str(blocks[idx] or "").strip()
        return ""
    if k == "pde.validator":
        from projects.services.pde import PDE_VALIDATOR_BOILERPLATE

        return str(PDE_VALIDATOR_BOILERPLATE or "").strip()
    if k == "pde.validator.boilerplate":
        from projects.services.pde import PDE_VALIDATOR_BOILERPLATE

        return str(PDE_VALIDATOR_BOILERPLATE or "").strip()
    if k == "pde.draft":
        from projects.services.pde import PDE_DRAFT_BOILERPLATE

        return str(PDE_DRAFT_BOILERPLATE or "").strip()
    if k == "pde.draft.boilerplate":
        from projects.services.pde import PDE_DRAFT_BOILERPLATE

        return str(PDE_DRAFT_BOILERPLATE or "").strip()
    if k == "cde.validator.boilerplate":
        from chats.services.cde import CDE_VALIDATOR_BOILERPLATE

        return str(CDE_VALIDATOR_BOILERPLATE or "").strip()
    if k == "cde.draft.boilerplate":
        from chats.services.cde import CDE_DRAFT_BOILERPLATE

        return str(CDE_DRAFT_BOILERPLATE or "").strip()
    if k == "ppde.validator":
        from projects.views_ppde_ui import PPDE_VALIDATOR_BOILERPLATE

        return str(PPDE_VALIDATOR_BOILERPLATE or "").strip()
    if k == "ppde.seed_summary":
        from projects.views_ppde_ui import PPDE_SEED_SUMMARY_BOILERPLATE

        return str(PPDE_SEED_SUMMARY_BOILERPLATE or "").strip()
    if k == "ppde.seed_purpose":
        from projects.views_ppde_ui import PPDE_SEED_PURPOSE_BOILERPLATE

        return str(PPDE_SEED_PURPOSE_BOILERPLATE or "").strip()
    if k == "ppde.stage_map":
        from projects.views_ppde_ui import PPDE_STAGE_MAP_BOILERPLATE

        return str(PPDE_STAGE_MAP_BOILERPLATE or "").strip()
    if k == "review.route_seed":
        return _REVIEW_ROUTE_SEED_TEXT
    if k == "review.intent_seed":
        return _REVIEW_INTENT_SEED_TEXT
    if k == "cko.review.system_block":
        from projects.views_review import build_cko_review_system_block

        return str(build_cko_review_system_block() or "").strip()
    if k == "review.execute_actions":
        return _REVIEW_EXECUTE_ACTIONS_TEXT
    if k == "rollup.summary":
        return _ROLLUP_TEXT
    return ""


def get_override(key: str) -> tuple[bool, str] | None:
    row = (
        ContractOverride.objects.filter(
            key=str(key or "").strip(),
            scope_type=ContractOverride.ScopeType.GLOBAL,
        )
        .order_by("-updated_at", "-id")
        .first()
    )
    if row is None:
        return None
    return bool(row.is_enabled), str(row.override_text or "")


def get_effective_text(key: str, ctx: Any | None = None) -> str:
    context = ctx if ctx is not None else _DefaultContext()
    raw = get_raw_contract_text(context, key)
    override = get_override(key)
    if override is None:
        return raw
    enabled, text = override
    if enabled:
        return str(text or "")
    return raw


def apply_override_for_block(key: str, raw_text: str, *, user=None, ctx: Any | None = None) -> tuple[str, dict]:
    scoped_key = map_block_key_to_contract_text_key(key, ctx=ctx)
    if scoped_key:
        resolved = resolve_contract_text(user, scoped_key)
        effective = str(resolved.get("effective_text") or "")
        if str(resolved.get("effective_source") or "") == "USER" or effective:
            return effective, {
                "applied": True,
                "reason": "contract_text",
                "contract_text_key": scoped_key,
                "effective_source": resolved.get("effective_source") or "",
            }

    override = get_override(key)
    if override is None:
        return raw_text, {"applied": False, "reason": "no_override"}
    enabled, text = override
    if not enabled:
        return raw_text, {"applied": False, "reason": "disabled"}
    return str(text or ""), {"applied": True, "reason": "enabled"}


class _DefaultContext:
    strict_json = True
    effective_context = {}
    chat = None
