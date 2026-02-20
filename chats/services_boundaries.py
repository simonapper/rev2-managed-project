# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, Dict, List


_DEFAULT_REQUIRED_LABELS = {
    "scope_flag": True,
    "assumptions": True,
    "source_basis": True,
    "confidence": True,
}

_DEFAULT_AUTHORITY_SET = {
    "allow_model_general_knowledge": True,
    "allow_internal_docs": True,
    "allow_public_sources": False,
}

_DEFAULT_RECENCY_TOPICS = ["TAX_RATES", "THRESHOLDS", "DEADLINES"]


def _split_topic_tags(raw: Any) -> List[str]:
    if isinstance(raw, list):
        values = [str(v or "").strip().upper() for v in raw]
    else:
        text = str(raw or "")
        text = text.replace(";", ",").replace("\n", ",")
        values = [v.strip().upper() for v in text.split(",")]
    out: List[str] = []
    for v in values:
        if not v:
            continue
        if v not in out:
            out.append(v)
    return out


def normalise_boundary_profile(raw: Dict[str, Any] | None) -> Dict[str, Any]:
    data = dict(raw or {})
    authority = dict(data.get("authority_set") or {})
    labels = dict(data.get("required_labels") or {})

    strictness = str(data.get("strictness") or "SOFT").strip().upper()
    if strictness not in {"SOFT"}:
        strictness = "SOFT"

    jurisdictions = _split_topic_tags(data.get("jurisdictions"))
    if not jurisdictions:
        single = str(data.get("jurisdiction") or "").strip()
        jurisdictions = [single] if single else ["NONE"]

    out = {
        "strictness": strictness,
        "jurisdiction": jurisdictions[0],
        "jurisdictions": jurisdictions,
        "topic_tags": _split_topic_tags(data.get("topic_tags")),
        "authority_set": {
            "allow_model_general_knowledge": bool(
                authority.get("allow_model_general_knowledge", _DEFAULT_AUTHORITY_SET["allow_model_general_knowledge"])
            ),
            "allow_internal_docs": bool(authority.get("allow_internal_docs", _DEFAULT_AUTHORITY_SET["allow_internal_docs"])),
            "allow_public_sources": bool(authority.get("allow_public_sources", _DEFAULT_AUTHORITY_SET["allow_public_sources"])),
        },
        "out_of_scope_behaviour": str(data.get("out_of_scope_behaviour") or "ALLOW_WITH_WARNING").strip().upper(),
        "recency_risk_topics": _split_topic_tags(data.get("recency_risk_topics") or _DEFAULT_RECENCY_TOPICS),
        "required_labels": {
            "scope_flag": bool(labels.get("scope_flag", _DEFAULT_REQUIRED_LABELS["scope_flag"])),
            "assumptions": bool(labels.get("assumptions", _DEFAULT_REQUIRED_LABELS["assumptions"])),
            "source_basis": bool(labels.get("source_basis", _DEFAULT_REQUIRED_LABELS["source_basis"])),
            "confidence": bool(labels.get("confidence", _DEFAULT_REQUIRED_LABELS["confidence"])),
        },
    }
    return out


def is_boundary_profile_active(boundary: Dict[str, Any] | None) -> bool:
    b = normalise_boundary_profile(boundary)
    topic_tags = [str(v or "").strip().upper() for v in (b.get("topic_tags") or [])]
    if any(topic_tags):
        return True
    jurisdictions = [str(v or "").strip().upper() for v in (b.get("jurisdictions") or [])]
    if any(j for j in jurisdictions if j and j != "NONE"):
        return True
    return False


def resolve_boundary_profile(project, chat) -> Dict[str, Any]:
    base = normalise_boundary_profile(getattr(project, "boundary_profile_json", {}) or {})
    chat_raw = getattr(chat, "boundary_profile_json", {}) or {}
    if chat_raw:
        merged = dict(base)
        for key in ("strictness", "jurisdiction", "out_of_scope_behaviour"):
            if key in chat_raw and chat_raw.get(key) not in (None, ""):
                merged[key] = chat_raw.get(key)
        if "jurisdictions" in chat_raw:
            merged["jurisdictions"] = _split_topic_tags(chat_raw.get("jurisdictions"))
        elif "jurisdiction" in chat_raw and chat_raw.get("jurisdiction") not in (None, ""):
            merged["jurisdictions"] = [str(chat_raw.get("jurisdiction")).strip()]

        if "topic_tags" in chat_raw:
            merged["topic_tags"] = _split_topic_tags(chat_raw.get("topic_tags"))
        if "recency_risk_topics" in chat_raw:
            merged["recency_risk_topics"] = _split_topic_tags(chat_raw.get("recency_risk_topics"))

        authority = dict(base.get("authority_set") or {})
        authority.update(dict(chat_raw.get("authority_set") or {}))
        merged["authority_set"] = authority

        labels = dict(base.get("required_labels") or {})
        labels.update(dict(chat_raw.get("required_labels") or {}))
        merged["required_labels"] = labels
        return normalise_boundary_profile(merged)
    return base


def build_boundary_contract_blocks(boundary: Dict[str, Any], excerpts: List[Dict[str, Any]]) -> List[str]:
    b = normalise_boundary_profile(boundary)
    topic_tags = [str(v or "").strip() for v in (b.get("topic_tags") or []) if str(v or "").strip()]
    lines = [
        "BOUNDARY CONTRACT",
        "Boundaries are SOFT: you may go beyond scope, but you must warn.",
        "Required labels in every answer:",
        "Scope: IN-SCOPE or OUT-OF-SCOPE",
        "Assumptions: include jurisdiction",
        "Source basis: policy_docs or general_knowledge",
        "Confidence: low or medium or high",
        "If you reference policy docs, cite as doc:<id> and title.",
        "Boundary constraints: " + (", ".join(topic_tags) if topic_tags else "none"),
        "Keep sentences short.",
    ]
    if "UK_TAX" in b.get("topic_tags", []):
        lines.append("For UK_TAX, warn about recency for rates, thresholds, and deadlines.")

    blocks = ["\n".join(lines)]
    if excerpts:
        ref_lines = ["POLICY EXCERPTS"]
        for ex in excerpts:
            ref_lines.append(
                "doc:{id} | {title}\n{excerpt}".format(
                    id=ex.get("doc_id"),
                    title=ex.get("title") or "",
                    excerpt=ex.get("excerpt") or "",
                )
            )
        blocks.append("\n\n".join(ref_lines))
    return blocks
