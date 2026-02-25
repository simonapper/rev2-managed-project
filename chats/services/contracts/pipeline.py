# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chats.services.cde_injection import build_cde_system_blocks
from chats.services.derax.envelope import build_derax_envelope_block
from chats.services.contracts.inspect import apply_override_for_block
from chats.services.contracts.boundary_resolver import resolve_boundary_contract
from chats.services.contracts.phase_resolver import resolve_phase_contract
from projects.services.llm_instructions import build_system_messages


@dataclass
class ContractContext:
    user: Any = None
    chat: Any = None
    project: Any = None
    work_item: Any = None
    active_phase: str = ""
    user_text: str = ""
    effective_context: dict | None = None
    boundary_excerpts: list[dict] = field(default_factory=list)
    ppde_phase_contract: Any = None
    is_rollup: bool = False
    is_review: bool = False
    is_pde: bool = False
    is_ppde: bool = False
    is_cde: bool = False
    is_derax: bool = False
    tier5_blocks: list[str] = field(default_factory=list)
    tier6_blocks: list[str] = field(default_factory=list)
    legacy_system_blocks: list[str] = field(default_factory=list)
    include_envelope: bool = True
    strict_json: bool = True


@dataclass(frozen=True)
class ContractBlock:
    key: str
    tier: int
    order: int
    title: str
    content: str
    source: str
    dedupe_group: str = ""


_ENVELOPE_BLOCK = (
    "Return JSON with keys:\n"
    "- answer: direct response\n"
    "- key_info: bullets / anchors\n"
    "- visuals: emojis, steps, breadcrumbs, ASCII diagrams\n"
    "- reasoning: reasoning summary\n"
    "- output: extractable artefact text\n"
    "Return strict JSON only. No markdown. No prose outside JSON."
)


def build_envelope_block(*, strict_json: bool = True) -> str:
    if strict_json:
        return _ENVELOPE_BLOCK
    return _ENVELOPE_BLOCK.replace(
        "Return strict JSON only. No markdown. No prose outside JSON.", ""
    ).strip()


def _legacy_dedupe_group(text: str) -> str:
    t = str(text or "").strip().upper()
    if "BOUNDARY CONTRACT" in t or "BOUNDARY_CONTRACT" in t:
        return "boundary"
    if "PHASE CONTRACT" in t or "WORKITEM_PHASE_CONTRACT" in t:
        return "phase_contract"
    return ""


def _dedupe_blocks(blocks: list[ContractBlock]) -> tuple[list[ContractBlock], list[dict]]:
    kept_by_group: dict[str, ContractBlock] = {}
    out: list[ContractBlock] = []
    dropped: list[dict] = []
    for block in sorted(blocks, key=lambda b: (b.tier, b.order, b.key)):
        group = str(block.dedupe_group or "").strip()
        if not group:
            out.append(block)
            continue
        prev = kept_by_group.get(group)
        if prev is None:
            kept_by_group[group] = block
            out.append(block)
            continue
        dropped.append(
            {
                "key": block.key,
                "source": block.source,
                "tier": block.tier,
                "order": block.order,
                "dedupe_group": group,
                "reason": f"deduped_by={prev.key}",
            }
        )
    return out, dropped


def build_system_blocks(ctx: ContractContext) -> tuple[list[str], dict]:
    blocks: list[ContractBlock] = []

    if ctx.include_envelope:
        blocks.append(
            ContractBlock(
                key="envelope.json_schema",
                tier=0,
                order=0,
                title="Core LLM envelope",
                content=build_envelope_block(strict_json=bool(ctx.strict_json)),
                source="pipeline.envelope",
            )
        )

    if isinstance(ctx.effective_context, dict):
        for idx, content in enumerate(build_system_messages(ctx.effective_context)):
            blocks.append(
                ContractBlock(
                    key=f"avatars.protocol.{idx}",
                    tier=1,
                    order=idx,
                    title="Avatar protocol",
                    content=str(content or "").strip(),
                    source="projects.services.llm_instructions",
                )
            )

    boundary = resolve_boundary_contract(ctx)
    effective_boundary = {}
    if boundary is not None and str(boundary.content or "").strip():
        effective_boundary = dict(boundary.effective_boundary or {})
        blocks.append(
            ContractBlock(
                key="boundary.effective",
                tier=2,
                order=0,
                title="Boundary Contract",
                content=boundary.content,
                source=boundary.source,
                dedupe_group="boundary",
            )
        )

    derax_mode = bool(getattr(ctx, "is_derax", False))
    if not derax_mode:
        project = getattr(ctx, "project", None)
        mode = str(getattr(project, "workflow_mode", "") or "").strip().upper()
        derax_mode = mode == "DERAX_WORK"
    if derax_mode:
        active_phase = str(
            getattr(ctx, "active_phase", "")
            or getattr(getattr(ctx, "work_item", None), "active_phase", "")
            or "DEFINE"
        ).strip().upper()
        blocks.append(
            ContractBlock(
                key="derax.envelope",
                tier=2,
                order=50,
                title="DERAX envelope",
                content=build_derax_envelope_block(active_phase),
                source="chats.services.derax.envelope",
            )
        )

    phase = resolve_phase_contract(ctx)
    effective_phase_contract = ""
    if phase is not None and str(phase.content or "").strip():
        effective_phase_contract = str(phase.effective_phase_contract or "")
        blocks.append(
            ContractBlock(
                key="phase.contract",
                tier=3,
                order=0,
                title="Phase Contract",
                content=phase.content,
                source=phase.source,
                dedupe_group="phase_contract",
            )
        )

    if bool(ctx.is_cde) and ctx.chat is not None:
        for idx, content in enumerate(build_cde_system_blocks(ctx.chat)):
            blocks.append(
                ContractBlock(
                    key=f"cde.contract.{idx}",
                    tier=4,
                    order=idx,
                    title="CDE contract",
                    content=str(content or "").strip(),
                    source="chats.services.cde_injection",
                )
            )

    for idx, content in enumerate(list(ctx.tier5_blocks or [])):
        c = str(content or "").strip()
        if not c:
            continue
        blocks.append(
            ContractBlock(
                key=f"flow.block.{idx}",
                tier=5,
                order=idx,
                title="Flow boilerplate",
                content=c,
                source="ctx.tier5_blocks",
                dedupe_group=_legacy_dedupe_group(c),
            )
        )

    for idx, content in enumerate(list(ctx.tier6_blocks or [])):
        c = str(content or "").strip()
        if not c:
            continue
        blocks.append(
            ContractBlock(
                key=f"content.block.{idx}",
                tier=6,
                order=idx,
                title="Content block",
                content=c,
                source="ctx.tier6_blocks",
            )
        )

    for idx, content in enumerate(list(ctx.legacy_system_blocks or [])):
        c = str(content or "").strip()
        if not c:
            continue
        blocks.append(
            ContractBlock(
                key=f"legacy.block.{idx}",
                tier=6,
                order=1000 + idx,
                title="Legacy block",
                content=c,
                source="legacy.system_blocks",
                dedupe_group=_legacy_dedupe_group(c),
            )
        )

    override_meta: dict[str, dict] = {}
    overridden_blocks: list[ContractBlock] = []
    for block in blocks:
        replaced_text, meta = apply_override_for_block(
            block.key,
            str(block.content or ""),
            user=getattr(ctx, "user", None),
            ctx=ctx,
        )
        override_meta[block.key] = meta
        overridden_blocks.append(
            ContractBlock(
                key=block.key,
                tier=block.tier,
                order=block.order,
                title=block.title,
                content=replaced_text,
                source=block.source,
                dedupe_group=block.dedupe_group,
            )
        )

    kept, dropped = _dedupe_blocks(overridden_blocks)
    ordered = sorted(kept, key=lambda b: (b.tier, b.order, b.key))
    system_blocks = [b.content for b in ordered if str(b.content or "").strip()]

    trace = {
        "ordered_blocks": [
            {
                "key": b.key,
                "source": b.source,
                "tier": b.tier,
                "order": b.order,
                "dedupe_group": b.dedupe_group,
                "override_applied": bool((override_meta.get(b.key) or {}).get("applied")),
            }
            for b in ordered
        ],
        "dropped_blocks": dropped,
        "effective_boundary": effective_boundary,
        "effective_phase_contract": effective_phase_contract,
        "override_meta": override_meta,
    }
    return system_blocks, trace
