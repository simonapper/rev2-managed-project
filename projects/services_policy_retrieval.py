# -*- coding: utf-8 -*-

from __future__ import annotations

import re
from typing import Any, Dict, List

from projects.models import PolicyDocument


_POLICY_HINT_WORDS = {
    "policy",
    "compliance",
    "regulation",
    "law",
    "tax",
    "hmrc",
    "threshold",
    "deadline",
    "rate",
}


def looks_policy_related(query_text: str) -> bool:
    text = str(query_text or "").lower()
    return any(word in text for word in _POLICY_HINT_WORDS)


def _tokenise(text: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9_]+", (text or "").lower()) if len(t) >= 3]


def _build_excerpt(body_text: str, token: str, max_chars: int) -> str:
    text = body_text or ""
    idx = text.lower().find(token.lower())
    if idx < 0:
        return text[:max_chars]
    half = max(80, int(max_chars / 2))
    start = max(0, idx - half)
    end = min(len(text), idx + half)
    return text[start:end]


def policy_retrieve(project, query_text: str, max_chars: int = 2000) -> List[Dict[str, Any]]:
    docs = list(
        PolicyDocument.objects.filter(project=project)
        .only("id", "title", "body_text", "source_ref")
        .order_by("-updated_at")[:40]
    )
    if not docs:
        return []

    tokens = _tokenise(query_text)
    if not tokens:
        return []

    scored: List[tuple[int, PolicyDocument, str]] = []
    for doc in docs:
        body = (doc.body_text or "").lower()
        best_score = 0
        best_token = ""
        for token in tokens:
            score = body.count(token)
            if score > best_score:
                best_score = score
                best_token = token
        if best_score > 0:
            scored.append((best_score, doc, best_token))

    scored.sort(key=lambda x: x[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for _score, doc, token in scored[:3]:
        out.append(
            {
                "doc_id": doc.id,
                "title": doc.title,
                "excerpt": _build_excerpt(doc.body_text or "", token, max_chars=max_chars),
                "source_ref": doc.source_ref or "",
            }
        )
    return out
