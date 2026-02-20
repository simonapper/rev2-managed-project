# -*- coding: utf-8 -*-
# projects/services/pde.py
#
# PDE v1 - Field validation and draft helpers (LLM only).
# NOTE: Keep all output JSON-only (handled by the LLM via the PDE boilerplate).
# NOTE: Keep code comments 7-bit ASCII only.

from __future__ import annotations

import ast
import json
import re
from typing import Any, Dict, Optional


PDE_VALIDATOR_BOILERPLATE = (
    "You are validating one project-definition field for clarity and stability.\n"
    "\n"
    "Classify the field value as exactly one:\n"
    "- PASS: clear, unambiguous, stable enough to lock.\n"
    "- WEAK: vague or underspecified; needs refinement.\n"
    "- CONFLICT: contradicts another locked field provided in context.\n"
    "\n"
    "Return OUTPUT as valid JSON only, matching this schema:\n"
    "{\n"
    '  "field_key": "string",\n'
    '  "verdict": "PASS | WEAK | CONFLICT",\n'
    '  "issues": ["string"],\n'
    '  "suggested_revision": "string",\n'
    '  "questions": ["string"],\n'
    '  "confidence": "LOW | MEDIUM | HIGH"\n'
    "}\n"
    "\n"
    "Rules:\n"
    "- No prose outside JSON in OUTPUT.\n"
    "- issues: empty if PASS.\n"
    "- questions: max 3, only if needed.\n"
    "- suggested_revision: provide a best improved rewrite even if WEAK/CONFLICT.\n"
)

PDE_DRAFT_BOILERPLATE = (
    "You turn free-form user intent into a draft Project CKO field set.\n"
    "Return JSON only, matching this schema:\n"
    "{\n"
    '  "hypotheses": {\n'
    '    "fields": {\n'
    '      "canonical.summary": "string",\n'
    '      "identity.project_type": "string",\n'
    '      "identity.project_status": "string",\n'
    '      "intent.primary_goal": "string",\n'
    '      "intent.success_criteria": "string",\n'
    '      "scope.in_scope": "string",\n'
    '      "scope.out_of_scope": "string",\n'
    '      "scope.hard_constraints": "string",\n'
    '      "authority.primary": "string",\n'
    '      "authority.secondary": "string",\n'
    '      "authority.deviation_rules": "string",\n'
    '      "posture.interpretive_rules": "string",\n'
    '      "posture.epistemic_constraints": "string",\n'
    '      "posture.novelty_rules": "string",\n'
    '      "storage.artefact_root_ref": "string",\n'
    '      "storage.canonical_artefact_types": "string",\n'
    '      "storage.non_authoritative": "string",\n'
    '      "context.narrative": "string"\n'
    "    }\n"
    "  }\n"
    "}\n"
    "\n"
    "Rules:\n"
    "- Keep canonical.summary to <=10 words.\n"
    "- Use enum values where possible:\n"
    "  - identity.project_type: META|KNOWLEDGE|DELIVERY|RESEARCH|OPERATIONS\n"
    "  - identity.project_status: ACTIVE|PAUSED|ARCHIVED\n"
    "- scope fields may be bullet lists in a single string.\n"
    "- Do not invent facts; reflect uncertainty.\n"
    "- If unknown, use an explicit placeholder like 'DEFERRED'.\n"
)

ALLOWED_PDE_SEED_STYLES = {"concise", "balanced", "detailed"}


def _normalise_seed_style(value: str) -> str:
    v = str(value or "").strip().lower()
    if v in ALLOWED_PDE_SEED_STYLES:
        return v
    return "balanced"


def _normalise_seed_constraints(value: str) -> str:
    text = str(value or "").strip()
    if len(text) > 400:
        return text[:400].strip()
    return text


def _seed_style_block(seed_style: str, seed_constraints: str = "") -> str:
    style = _normalise_seed_style(seed_style)
    lines = ["Writing style controls:"]
    if style == "concise":
        lines.extend(
            [
                "- Use short sentences.",
                "- One idea per sentence.",
                "- Avoid qualifiers and filler.",
                "- Avoid wording like high-level, robust, comprehensive, strategic.",
                "- Prefer concrete verbs and clear actions.",
            ]
        )
    elif style == "detailed":
        lines.extend(
            [
                "- Use clear detail with practical depth.",
                "- Prefer concrete specifics over abstract terms.",
                "- Keep structure explicit and scannable.",
            ]
        )
    else:
        lines.extend(
            [
                "- Use balanced clarity.",
                "- Avoid unnecessary qualifiers.",
                "- Keep language practical and direct.",
            ]
        )
    constraints = _normalise_seed_constraints(seed_constraints)
    if constraints:
        lines.append("- User constraints: " + constraints)
    return "\n".join(lines)


def _locked_fields_block(locked_fields: Dict[str, str]) -> str:
    if not locked_fields:
        return "Locked fields context: (none)\n"
    keys = sorted(locked_fields.keys())
    lines = ["Locked fields context:"]
    for k in keys:
        v = (locked_fields.get(k) or "").strip()
        if not v:
            continue
        lines.append("- " + k + ": " + v)
    if len(lines) == 1:
        lines.append("(none)")
    return "\n".join(lines) + "\n"


def _parse_output_json(raw_output: str, field_key: str) -> Dict[str, Any]:
    raw_output = (raw_output or "").strip()
    data = None
    try:
        data = json.loads(raw_output)
    except Exception:
        data = _extract_json_object(raw_output)
    if data is None:
        return {
            "field_key": field_key,
            "verdict": "WEAK",
            "issues": ["OUTPUT was not valid JSON."],
            "suggested_revision": ("" if not raw_output else raw_output),
            "questions": ["Re-run verify; if persists, check prompt/contract."],
            "confidence": "LOW",
        }

    if not isinstance(data, dict):
        return {
            "field_key": field_key,
            "verdict": "WEAK",
            "issues": ["OUTPUT JSON was not an object."],
            "suggested_revision": "",
            "questions": ["Re-run verify; if persists, check prompt/contract."],
            "confidence": "LOW",
        }

    out: Dict[str, Any] = {}
    out["field_key"] = str(data.get("field_key") or field_key)

    verdict = str(data.get("verdict") or "").strip().upper()
    if verdict not in ("PASS", "WEAK", "CONFLICT"):
        verdict = "WEAK"
    out["verdict"] = verdict

    issues = data.get("issues")
    if isinstance(issues, list):
        out["issues"] = [str(x) for x in issues if str(x).strip()]
    else:
        out["issues"] = []

    out["suggested_revision"] = str(data.get("suggested_revision") or "")

    questions = data.get("questions")
    if isinstance(questions, list):
        qs = [str(x) for x in questions if str(x).strip()]
        out["questions"] = qs[:3]
    else:
        out["questions"] = []

    confidence = str(data.get("confidence") or "").strip().upper()
    if confidence not in ("LOW", "MEDIUM", "HIGH"):
        confidence = "LOW"
    out["confidence"] = confidence

    if out["verdict"] == "PASS":
        out["issues"] = []

    return out


def _word_tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z0-9']+", str(text or "")) if w]


def _normalise_canonical_summary(text: str) -> str:
    # Single sentence, plain text, max 15 words.
    raw = str(text or "").replace("\r", " ").replace("\n", " ").strip()
    raw = re.sub(r"\s+", " ", raw)
    if not raw:
        return ""
    words = _word_tokens(raw)
    if len(words) > 15:
        words = words[:15]
    out = " ".join(words).strip()
    if not out:
        return ""
    out = out.rstrip(".!?") + "."
    return out


def _extract_json_object(raw_text: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw_text, dict):
        return raw_text
    if isinstance(raw_text, list):
        for item in raw_text:
            if isinstance(item, dict):
                return item

    text = ("" if raw_text is None else str(raw_text)).strip()
    if not text:
        return None

    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    if "```" in text:
        parts = text.split("```")
        for chunk in parts[1::2]:
            candidate = chunk.strip()
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                continue

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
        try:
            obj = ast.literal_eval(candidate)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

    if start != -1:
        depth = 0
        in_str = False
        escaped = False
        for i, ch in enumerate(text[start:], start):
            if escaped:
                escaped = False
                continue
            if ch == "\\" and in_str:
                escaped = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict):
                            return obj
                    except Exception:
                        break

    try:
        obj = ast.literal_eval(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    return None


def validate_field(
    *,
    generate_panes_func,
    field_key: str,
    value_text: str,
    locked_fields: Optional[Dict[str, str]] = None,
    rubric: Optional[str] = None,
) -> Dict[str, Any]:
    locked_fields = locked_fields or {}
    value_text = (value_text or "").strip()

    system_blocks = [PDE_VALIDATOR_BOILERPLATE]

    rr = (rubric or "").strip()
    if rr:
        system_blocks.append("Field rubric (apply lightly):\n" + rr + "\n")

    system_blocks.append(_locked_fields_block(locked_fields))

    llm_input = "Field key: " + field_key + "\n" + "Field value:\n" + value_text

    panes = generate_panes_func(
        llm_input,
        image_parts=None,
        system_blocks=system_blocks,
    )
    pane_order = ("output", "answer", "reasoning", "key_info", "visuals")
    raw_first = ""
    parsed_obj = None
    for k in pane_order:
        text = str(panes.get(k) or "").strip()
        if not text:
            continue
        if not raw_first:
            raw_first = text
        parsed_obj = _extract_json_object(text)
        if isinstance(parsed_obj, dict):
            break

    if isinstance(parsed_obj, dict):
        out = _parse_output_json(
            raw_output=json.dumps(parsed_obj, ensure_ascii=True),
            field_key=field_key,
        )
    else:
        out = _parse_output_json(
            raw_output=raw_first,
            field_key=field_key,
        )

    if field_key == "canonical.summary":
        value_words = len(_word_tokens(value_text))
        if value_words > 15:
            out["verdict"] = "WEAK"
            issues = [str(x) for x in (out.get("issues") or []) if str(x).strip()]
            msg = "Canonical summary must be one sentence and 15 words or fewer."
            if msg not in issues:
                issues.append(msg)
            out["issues"] = issues
        suggested = _normalise_canonical_summary(out.get("suggested_revision") or "")
        if not suggested:
            suggested = _normalise_canonical_summary(value_text)
        out["suggested_revision"] = suggested

    out["debug_system_blocks"] = system_blocks
    out["debug_user_text"] = llm_input
    return out


def draft_pde_from_seed(
    *,
    generate_panes_func,
    seed_text: str,
    seed_style: str = "balanced",
    seed_constraints: str = "",
) -> Dict[str, Any]:
    seed_text = (seed_text or "").strip()
    system_blocks = [PDE_DRAFT_BOILERPLATE, _seed_style_block(seed_style, seed_constraints)]
    panes = generate_panes_func(
        "Seed intent:\n" + seed_text,
        image_parts=None,
        system_blocks=system_blocks,
    )
    pane_order = ("output", "answer", "reasoning", "key_info", "visuals")
    candidates = [(panes.get(k) or "") for k in pane_order]
    pane_dump_lines = []
    for k in pane_order:
        val = str(panes.get(k) or "").strip()
        if val:
            pane_dump_lines.append(k.upper() + ":\n" + val)
    pane_dump = "\n\n".join(pane_dump_lines).strip()
    if not pane_dump:
        try:
            pane_dump = "PANE_DEBUG:\n" + json.dumps(
                {k: str(panes.get(k) or "") for k in pane_order},
                ensure_ascii=True,
                indent=2,
            )
        except Exception:
            pane_dump = "PANE_DEBUG: unavailable"
    raw = ""
    data = None
    for c in candidates:
        text = str(c or "").strip()
        if not text:
            continue
        if not raw:
            raw = text
        data = _extract_json_object(text)
        if data is not None:
            raw = text
            break
    if data is None:
        if not (raw or "").strip() and pane_dump.startswith("PANE_DEBUG:"):
            return {
                "ok": False,
                "error": "Draft model returned empty panes.",
                "raw": pane_dump,
            }
        return {
            "ok": False,
            "error": "Draft OUTPUT was not valid JSON.",
            "raw": (raw or pane_dump),
        }

    hyp = data.get("hypotheses") if isinstance(data, dict) else None
    if not isinstance(hyp, dict):
        return {
            "ok": False,
            "error": "Draft JSON missing hypotheses.",
            "raw": (raw or pane_dump),
        }

    fields = hyp.get("fields")
    if not isinstance(fields, dict):
        return {
            "ok": False,
            "error": "Draft JSON missing hypotheses.fields.",
            "raw": (raw or pane_dump),
        }

    out_fields: Dict[str, str] = {}
    for k, v in fields.items():
        kk = str(k).strip()
        if not kk:
            continue
        out_fields[kk] = ("" if v is None else str(v)).strip()

    return {"ok": True, "draft": {"hypotheses": {"fields": out_fields}}, "raw": (raw or pane_dump)}
