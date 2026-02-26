# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from typing import Any

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.utils import timezone

from chats.services.derax.generate import build_docx_for_markdown
from projects.models import Project, ProjectDocument, WorkItem


def _read_json_document(doc: ProjectDocument) -> dict | None:
    try:
        doc.file.open("rb")
        try:
            raw = doc.file.read()
        finally:
            doc.file.close()
    except Exception:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    try:
        payload = json.loads(str(raw or "{}"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _list_markdown(lines: list[str], values: list[str]) -> None:
    if not values:
        lines.append("- (none)")
        return
    for item in values:
        text = str(item or "").strip()
        if text:
            lines.append("- " + text)


def _latest_non_empty(payloads: list[dict], path: list[str]) -> str:
    for payload in reversed(payloads):
        node: Any = payload
        ok = True
        for key in path:
            if not isinstance(node, dict):
                ok = False
                break
            node = node.get(key)
        if ok and isinstance(node, str) and node.strip():
            return node.strip()
    return ""


def _payload_phase(payload: dict) -> str:
    meta = payload.get("meta")
    if isinstance(meta, dict):
        phase = str(meta.get("phase") or "").strip().upper()
        if phase:
            return phase
    return str(payload.get("phase") or "").strip().upper()


def _extract_destination(payload: dict) -> str:
    intent = payload.get("intent")
    if isinstance(intent, dict):
        text = str(intent.get("destination") or "").strip()
        if text:
            return text
    core = payload.get("core")
    if isinstance(core, dict):
        return str(core.get("end_in_mind") or "").strip()
    return ""


def _extract_success_criteria(payload: dict) -> list[str]:
    intent = payload.get("intent")
    if isinstance(intent, dict):
        return [str(v or "").strip() for v in list(intent.get("success_criteria") or []) if str(v or "").strip()]
    core = payload.get("core")
    if isinstance(core, dict):
        return [str(v or "").strip() for v in list(core.get("destination_conditions") or []) if str(v or "").strip()]
    return []


def _extract_non_goals(payload: dict) -> list[str]:
    intent = payload.get("intent")
    if isinstance(intent, dict):
        return [str(v or "").strip() for v in list(intent.get("non_goals") or []) if str(v or "").strip()]
    core = payload.get("core")
    if isinstance(core, dict):
        return [str(v or "").strip() for v in list(core.get("non_goals") or []) if str(v or "").strip()]
    return []


def compile_derax_run_to_cko_markdown(payloads: list[dict]) -> str:
    rows = [p for p in list(payloads or []) if isinstance(p, dict)]
    define_rows = [p for p in rows if _payload_phase(p) == "DEFINE"]
    explore_rows = [p for p in rows if _payload_phase(p) == "EXPLORE"]
    define_payload = define_rows[-1] if define_rows else (rows[-1] if rows else {})

    summary = _latest_non_empty(rows, ["canonical_summary"])
    if not summary:
        summary = _extract_destination(define_payload)
    if not summary:
        summary = _latest_non_empty(rows, ["headline"])
    if not summary:
        summary = "(none)"

    adjacent: list[str] = []
    risks: list[str] = []
    tradeoffs: list[str] = []
    reframes: list[str] = []
    footnotes: list[str] = []
    provenance: list[str] = []

    for payload in explore_rows:
        explore = dict(payload.get("explore") or {})
        core = dict(payload.get("core") or {})
        adjacent.extend(
            [
                str(v or "").strip()
                for v in (list(explore.get("adjacent_ideas") or []) + list(core.get("adjacent_angles") or []))
                if str(v or "").strip()
            ]
        )
        risks.extend([str(v or "").strip() for v in (list(explore.get("risks") or []) + list(core.get("risks") or [])) if str(v or "").strip()])
        tradeoffs.extend(
            [str(v or "").strip() for v in (list(explore.get("tradeoffs") or []) + list(core.get("scope_changes") or [])) if str(v or "").strip()]
        )
        reframes.extend([str(v or "").strip() for v in (list(explore.get("reframes") or []) + list(core.get("ambiguities") or [])) if str(v or "").strip()])

    for payload in rows:
        parked = dict(payload.get("parked_for_later") or {})
        for item in list(parked.get("items") or []):
            if isinstance(item, dict):
                title = str(item.get("title") or "").strip()
                detail = str(item.get("detail") or "").strip()
                combined = title if not detail else f"{title}: {detail}" if title else detail
                if combined:
                    footnotes.append(combined)
        for item in list(payload.get("parked") or []):
            text = str(item or "").strip()
            if text:
                footnotes.append(text)
        for item in list(payload.get("footnotes") or []):
            text = str(item or "").strip()
            if text:
                footnotes.append(text)
        ts = str((payload.get("meta") or {}).get("timestamp") or "").strip()
        phase = _payload_phase(payload)
        if ts or phase:
            provenance.append(f"{ts or '?'} | {phase or '?'}")

    lines = [
        "# DERAX Compiled CKO",
        "",
        "## Canonical Summary",
        "",
        summary,
        "",
        "## Destination",
        "",
        _extract_destination(define_payload) or "(none)",
        "",
        "## Success Criteria",
        "",
    ]
    _list_markdown(lines, _extract_success_criteria(define_payload))
    lines.extend(["", "## Non-goals", ""])
    _list_markdown(lines, _extract_non_goals(define_payload))
    lines.extend(["", "## Explore", ""])
    lines.append("### Adjacent Ideas")
    _list_markdown(lines, adjacent)
    lines.append("")
    lines.append("### Risks")
    _list_markdown(lines, risks)
    lines.append("")
    lines.append("### Tradeoffs")
    _list_markdown(lines, tradeoffs)
    lines.append("")
    lines.append("### Reframes")
    _list_markdown(lines, reframes)
    lines.extend(["", "## Footnotes", ""])
    _list_markdown(lines, footnotes)
    lines.extend(["", "## Provenance", ""])
    _list_markdown(lines, provenance)
    return "\n".join(lines).strip() + "\n"


def load_derax_chat_payloads(*, project_id: int, chat_id: int) -> list[dict]:
    docs = (
        ProjectDocument.objects
        .filter(project_id=int(project_id), original_name__startswith=f"derax/{int(chat_id)}/", original_name__iendswith=".json")
        .order_by("created_at", "id")
    )
    out: list[dict] = []
    for doc in docs:
        payload = _read_json_document(doc)
        if payload is not None:
            out.append(payload)
    return out


def _persist_docx_doc(
    *,
    project: Project,
    chat_id: int,
    title: str,
    markdown: str,
    user_id: int | None = None,
) -> ProjectDocument:
    user_model = get_user_model()
    user = user_model.objects.filter(id=int(user_id or 0)).first() if user_id else None
    if user is None:
        user = project.owner
    body = build_docx_for_markdown(markdown)
    rel_name = f"derax/{int(chat_id)}/compiled_cko.docx"
    doc = ProjectDocument(
        project=project,
        title=(title or "DERAX Compiled CKO")[:200],
        original_name=rel_name[:255],
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=len(body),
        uploaded_by=user,
    )
    doc.file.save(rel_name, ContentFile(body), save=False)
    doc.save()
    return doc


def compile_derax_chat_run_to_cko_artefact(*, project_id: int, chat_id: int, title: str) -> str:
    project = Project.objects.filter(id=int(project_id)).first()
    if project is None:
        raise ValueError("Project not found.")
    payloads = load_derax_chat_payloads(project_id=int(project_id), chat_id=int(chat_id))
    markdown = compile_derax_run_to_cko_markdown(payloads)
    doc = _persist_docx_doc(project=project, chat_id=int(chat_id), title=title, markdown=markdown)
    return str(doc.id)


# Backward-compatible helper used by existing code/tests.
def compile_derax_to_cko(work_item: WorkItem) -> str:
    payloads: list[dict] = []
    for run in list(getattr(work_item, "derax_runs", []) or []):
        if not isinstance(run, dict):
            continue
        asset_id = int(run.get("asset_id") or 0)
        if asset_id <= 0:
            continue
        doc = ProjectDocument.objects.filter(id=asset_id, project=work_item.project).first()
        if doc is None:
            continue
        payload = _read_json_document(doc)
        if payload is not None:
            payloads.append(payload)
    return compile_derax_run_to_cko_markdown(payloads)


# Backward-compatible helper used by existing code/tests.
def persist_compiled_cko(work_item: WorkItem, *, user) -> ProjectDocument:
    markdown = compile_derax_to_cko(work_item)
    doc = _persist_docx_doc(
        project=work_item.project,
        chat_id=int(work_item.id),
        title=f"CKO {int(work_item.id)}",
        markdown=markdown,
        user_id=getattr(user, "id", None),
    )
    runs = list(getattr(work_item, "derax_runs", []) or [])
    runs.append(
        {
            "phase": "CKO",
            "asset_id": int(doc.id),
            "created_at": timezone.now().isoformat(),
            "path": str(doc.file.name or ""),
            "turn_id": "",
        }
    )
    work_item.derax_runs = runs
    work_item.save(update_fields=["derax_runs", "updated_at"])
    return doc
