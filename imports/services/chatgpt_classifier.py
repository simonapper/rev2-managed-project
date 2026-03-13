# -*- coding: utf-8 -*-
# imports/services/chatgpt_classifier.py
"""
ChatGPT import helpers: classify/split assistant text into panes.

Goal (v1):
- Prefer explicit pane headers when present.
- Otherwise use lightweight heuristics (safe + predictable).
- Keep this file pure-Python (no Django imports) so it is easy to unit test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# ----------------------------
# Public API
# ----------------------------

PANE_KEYS = ("answer", "reasoning", "output", "key_info", "visuals", "uncategorised")


def classify_assistant_text(text: str) -> str:
    """
    Classify a single assistant text blob into a coarse bucket.

    This is mainly for legacy imports where you had multiple "streams"
    (answer/reasoning/output/sources/visuals) stored as separate messages.
    """
    panes = split_assistant_text_into_panes(text)
    # Prefer the richest non-empty pane in a stable order.
    for k in ("answer", "output", "reasoning", "key_info", "visuals"):
        if (panes.get(k) or "").strip():
            return k
    return "uncategorised"


def split_assistant_text_into_panes(text: str) -> Dict[str, str]:
    """
    Split a single assistant message into panes.

    Returns:
      {
        "answer": "...",
        "reasoning": "...",
        "output": "...",
        "key_info": "...",
        "visuals": "...",
        "uncategorised": "..."
      }
    """
    raw = (text or "").strip()
    panes = {k: "" for k in PANE_KEYS}
    if not raw:
        return panes

    # 1) If the assistant used explicit headers, obey them.
    segmented = _split_by_headers(raw)
    if segmented:
        for k, v in segmented.items():
            if k in panes:
                panes[k] = v.strip()
        return panes

    # 2) Otherwise: heuristics (keep simple + predictable).
    # Code-heavy => output
    if _looks_like_code_output(raw):
        panes["output"] = raw
        return panes

    # Link/citation-heavy => key_info
    if _looks_like_sources_or_key_info(raw):
        panes["key_info"] = raw
        return panes

    # Visual cue => visuals
    if _looks_like_visuals(raw):
        panes["visuals"] = raw
        return panes

    # Reasoning cue => reasoning
    if _looks_like_reasoning(raw):
        panes["reasoning"] = raw
        return panes

    # Default => answer
    panes["answer"] = raw
    return panes


# ----------------------------
# Header-based splitting
# ----------------------------

# Map many possible headers to internal pane keys.
_HEADER_MAP = {
    # Answer
    "answer": "answer",
    "final": "answer",
    "response": "answer",
    # Reasoning
    "reasoning": "reasoning",
    "analysis": "reasoning",
    "rationale": "reasoning",
    "thinking": "reasoning",
    # Output
    "output": "output",
    "code": "output",
    "implementation": "output",
    "patch": "output",
    "diff": "output",
    # Key info / sources
    "key info": "key_info",
    "keyinfo": "key_info",
    "sources": "key_info",
    "references": "key_info",
    "citations": "key_info",
    # Visuals
    "visuals": "visuals",
    "images": "visuals",
    "figures": "visuals",
    "diagrams": "visuals",
}

# Match headers like:
#   ANSWER:
#   Answer:
#   ## ANSWER:
#   ### Key info:
_HEADER_RE = re.compile(
    r"(?im)^(?:\s{0,3}(?:#{1,6}\s*)?)"
    r"(?P<h>[A-Za-z][A-Za-z0-9 _-]{1,30})"
    r"\s*:\s*$"
)


def _normalise_header(h: str) -> str:
    h2 = (h or "").strip().lower()
    h2 = h2.replace("-", " ").replace("_", " ")
    h2 = re.sub(r"\s+", " ", h2)
    return h2


def _split_by_headers(text: str) -> Optional[Dict[str, str]]:
    """
    If we find >=2 recognised headers, split into sections.
    If only 1 header is present, we still split (header -> end).
    If no recognised headers, return None.
    """
    matches = list(_HEADER_RE.finditer(text))
    if not matches:
        return None

    # Keep only recognised headers.
    recognised: List[Tuple[int, int, str]] = []
    for m in matches:
        h_raw = m.group("h")
        h_norm = _normalise_header(h_raw)
        pane_key = _HEADER_MAP.get(h_norm)
        if pane_key:
            recognised.append((m.start(), m.end(), pane_key))

    if not recognised:
        return None

    # Build slices between recognised headers.
    out: Dict[str, str] = {}
    for idx, (start, end, pane_key) in enumerate(recognised):
        body_start = end
        body_end = recognised[idx + 1][0] if idx + 1 < len(recognised) else len(text)
        body = text[body_start:body_end].strip("\n")
        # Append if repeated header appears (rare, but safe).
        out[pane_key] = (out.get(pane_key, "") + "\n\n" + body).strip() if out.get(pane_key) else body.strip()

    # If we only got one recognised header, treat any leading text as "answer" (optional)
    if len(recognised) == 1:
        lead = text[:recognised[0][0]].strip()
        if lead and "answer" not in out:
            out["answer"] = lead

    return out if out else None


# ----------------------------
# Heuristics
# ----------------------------

_CODE_FENCE_RE = re.compile(r"```")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_MD_REF_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")  # markdown links
_BULLET_HEAVY_RE = re.compile(r"(?m)^(?:\s*[-*]\s+|\s*\d+\.\s+).+")


def _looks_like_code_output(t: str) -> bool:
    if _CODE_FENCE_RE.search(t):
        return True
    # Lots of code-ish punctuation + indentation
    lines = t.splitlines()
    if len(lines) >= 6:
        indented = sum(1 for ln in lines if ln.startswith(("    ", "\t")))
        if indented >= max(3, len(lines) // 3):
            return True
    # Obvious snippets
    if any(tok in t for tok in ("def ", "class ", "import ", "SELECT ", "INSERT ", "UPDATE ", "{", "};")):
        # Keep it conservative: require at least a little multi-line structure.
        return "\n" in t and len(t) > 80
    return False


def _looks_like_sources_or_key_info(t: str) -> bool:
    if _URL_RE.search(t) or _MD_REF_RE.search(t):
        return True
    low = t.lower()
    if any(w in low for w in ("source:", "sources:", "reference:", "references:", "citation", "citations")):
        return True
    return False


def _looks_like_visuals(t: str) -> bool:
    low = t.lower()
    return any(w in low for w in ("diagram", "figure", "chart", "graph", "image", "screenshot"))


def _looks_like_reasoning(t: str) -> bool:
    low = t.lower()
    # Conservative cues
    cues = (
        "because",
        "therefore",
        "so that",
        "this implies",
        "trade-off",
        "trade off",
        "assumption",
        "alternatively",
        "pros and cons",
        "why:",
    )
    if any(c in low for c in cues):
        return True
    # Multi-step explanatory bullets often indicate reasoning
    if _BULLET_HEAVY_RE.search(t) and any(w in low for w in ("first", "second", "then", "next")):
        return True
    return False
