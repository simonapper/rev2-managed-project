# -*- coding: utf-8 -*-

from __future__ import annotations

from chats.services.derax.contracts import DERAX_PHASES


def _phase_name_from_key(key: str) -> str:
    raw = str(key or "").strip().lower()
    if not raw.startswith("phase."):
        return ""
    phase = raw.split(".", 1)[1].strip().upper()
    return phase if phase in DERAX_PHASES else ""


def lint_contract_text(*, key: str, text: str) -> dict:
    phase = _phase_name_from_key(key)
    findings: list[dict] = []
    raw = str(text or "")
    low = raw.lower()

    if not phase:
        return {"ok": True, "phase": "", "findings": findings}

    if "json" not in low:
        findings.append(
            {
                "severity": "WARN",
                "code": "missing_json_guardrail",
                "message": "Contract does not explicitly require JSON output.",
            }
        )

    if "markdown" in low and "no markdown" not in low:
        findings.append(
            {
                "severity": "BLOCK",
                "code": "markdown_allowed",
                "message": "Contract appears to allow markdown output.",
            }
        )

    if phase != "DEFINE":
        blocked = [f for f in findings if str(f.get("severity")) == "BLOCK"]
        return {"ok": len(blocked) == 0, "phase": phase, "findings": findings}

    lines = [str(line or "").strip().lower() for line in raw.splitlines() if str(line or "").strip()]
    for line in lines:
        if "success criteria" in line:
            if "do not" not in line and "[]" not in line and "empty" not in line:
                findings.append(
                    {
                        "severity": "BLOCK",
                        "code": "define_success_criteria_forbidden",
                        "message": "DEFINE contract must not instruct generation of success criteria.",
                    }
                )
        if "artefacts.proposed" in line:
            if "[]" not in line and "empty" not in line and "do not" not in line:
                findings.append(
                    {
                        "severity": "BLOCK",
                        "code": "define_artefacts_forbidden",
                        "message": "DEFINE contract must not instruct artefacts.proposed content.",
                    }
                )

    if "subtext" not in low:
        findings.append(
            {
                "severity": "WARN",
                "code": "define_missing_subtext_probe",
                "message": "DEFINE contract is missing explicit subtext probing guidance.",
            }
        )
    if "1-3" not in low and "1 to 3" not in low:
        findings.append(
            {
                "severity": "WARN",
                "code": "define_missing_question_cap",
                "message": "DEFINE contract does not state 1-3 clarification question limit.",
            }
        )
    if "hypothesis:" not in low:
        findings.append(
            {
                "severity": "WARN",
                "code": "define_missing_hypothesis_prefix",
                "message": "DEFINE contract does not require HYPOTHESIS prefix in assumptions.",
            }
        )

    blocked = [f for f in findings if str(f.get("severity")) == "BLOCK"]
    return {"ok": len(blocked) == 0, "phase": phase, "findings": findings}


def contract_lint_errors(*, key: str, text: str) -> list[str]:
    result = lint_contract_text(key=key, text=text)
    out: list[str] = []
    for row in list(result.get("findings") or []):
        if str((row or {}).get("severity") or "") != "BLOCK":
            continue
        message = str((row or {}).get("message") or "").strip()
        if message:
            out.append(message)
    return out
