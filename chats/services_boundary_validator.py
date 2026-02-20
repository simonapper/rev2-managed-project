# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _has_label_line(text: str, label: str) -> bool:
    for line in (text or "").splitlines():
        if line.strip().lower().startswith(label.lower() + ":"):
            return True
    return False


def validate_boundary_labels(
    boundary_profile: Dict[str, Any] | None,
    model_text: str,
    *,
    boundary_excerpts: List[Dict[str, Any]] | None = None,
) -> Tuple[bool, List[str]]:
    profile = dict(boundary_profile or {})
    required = dict(profile.get("required_labels") or {})
    text = (model_text or "").strip()
    errors: List[str] = []

    if required.get("scope_flag", True) and not _has_label_line(text, "Scope"):
        errors.append("missing Scope")
    if required.get("assumptions", True) and not _has_label_line(text, "Assumptions"):
        errors.append("missing Assumptions")
    if required.get("source_basis", True) and not _has_label_line(text, "Source basis"):
        errors.append("missing Source basis")
    if required.get("confidence", True) and not _has_label_line(text, "Confidence"):
        errors.append("missing Confidence")

    excerpts = boundary_excerpts or []
    if excerpts:
        lower = text.lower()
        claims_policy = ("source basis:" in lower) and ("policy_docs" in lower)
        if claims_policy:
            has_ref = False
            for ex in excerpts:
                doc_id = str(ex.get("doc_id") or "").strip()
                title = str(ex.get("title") or "").strip()
                if doc_id and ("doc:" + doc_id) in lower:
                    has_ref = True
                    break
                if title and title.lower() in lower:
                    has_ref = True
                    break
            if not has_ref:
                errors.append("policy source basis without doc citation")

    return (len(errors) == 0, errors)
