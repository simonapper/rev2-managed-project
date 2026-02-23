# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chats.services_boundaries import build_boundary_contract_blocks, normalise_boundary_profile, resolve_boundary_profile


@dataclass(frozen=True)
class BoundaryResolution:
    content: str
    source: str
    effective_boundary: dict


def _normalise_required_labels(raw_value: Any) -> dict | Any:
    if not isinstance(raw_value, list):
        return raw_value
    labels = {str(v or "").strip().lower() for v in raw_value if str(v or "").strip()}
    return {
        "scope_flag": "scope" in labels,
        "assumptions": "assumptions" in labels,
        "source_basis": "source basis" in labels or "source_basis" in labels,
        "confidence": "confidence" in labels,
    }


def resolve_boundary_contract(ctx: Any) -> BoundaryResolution | None:
    project = getattr(ctx, "project", None)
    chat = getattr(ctx, "chat", None)
    work_item = getattr(ctx, "work_item", None)
    excerpts = list(getattr(ctx, "boundary_excerpts", []) or [])

    base = resolve_boundary_profile(project, chat)
    source = "project/chat"

    wi_raw = {}
    if work_item is not None:
        wi_raw = dict(getattr(work_item, "boundary_profile_json", {}) or {})
    if wi_raw:
        merged = dict(base)
        for key in (
            "strictness",
            "jurisdiction",
            "jurisdictions",
            "topic_tags",
            "authority_set",
            "out_of_scope_behaviour",
            "recency_risk_topics",
            "required_labels",
        ):
            if key in wi_raw and wi_raw.get(key) not in (None, "", []):
                value = wi_raw.get(key)
                if key == "required_labels":
                    value = _normalise_required_labels(value)
                merged[key] = value
                if key == "jurisdiction":
                    merged["jurisdictions"] = [str(value or "").strip()]
        base = normalise_boundary_profile(merged)
        source = "workitem+project/chat"

    if not base:
        return None

    blocks = build_boundary_contract_blocks(base, excerpts)
    if not blocks:
        return None
    return BoundaryResolution(content=str(blocks[0] or "").strip(), source=source, effective_boundary=base)
