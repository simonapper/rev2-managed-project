# chats/services/segmentation.py
# -*- coding: utf-8 -*-
# Create a small helper that takes raw_text and returns answer_text / reasoning_text / output_text + meta.

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Segments:
    answer: str
    reasoning: str
    output: str
    meta: dict


def segment_assistant_text(raw_text: str, *, parser_version: str = "v1") -> Segments:
    text = raw_text or ""

    a_key = "ANSWER:"
    r_key = "REASONING:"
    o_key = "OUTPUT:"

    a_i = text.find(a_key)
    r_i = text.find(r_key)
    o_i = text.find(o_key)

    if a_i != -1 and r_i != -1 and o_i != -1 and a_i < r_i < o_i:
        answer = text[a_i + len(a_key) : r_i].strip()
        reasoning = text[r_i + len(r_key) : o_i].strip()
        output = text[o_i + len(o_key) :].strip()
        return Segments(
            answer=answer,
            reasoning=reasoning,
            output=output,
            meta={"parser_version": parser_version, "confidence": "HIGH"},
        )

    # Fallback: whole thing is answer
    return Segments(
        answer=text.strip(),
        reasoning="",
        output="",
        meta={"parser_version": parser_version, "confidence": "LOW", "extraction_notes": "Missing required headers"},
    )
