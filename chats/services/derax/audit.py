# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re

from django.core.files.base import ContentFile
from django.utils import timezone

from projects.models import ProjectDocument, WorkItem


def _safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    cleaned = cleaned.strip("._-")
    return cleaned or "Project"


def _json_block(value) -> str:
    return json.dumps(value, ensure_ascii=True, indent=2)


def build_derax_project_audit_text(work_item: WorkItem) -> str:
    project = work_item.project
    created = timezone.now().isoformat()
    active_seed_text = ""
    for row in list(work_item.seed_log or []):
        if not isinstance(row, dict):
            continue
        if int(row.get("revision") or 0) == int(work_item.active_seed_revision or 0):
            active_seed_text = str(row.get("seed_text") or "").strip()
            break

    approved_steps = []
    for row in list(work_item.seed_log or []):
        if not isinstance(row, dict):
            continue
        reason = str(row.get("reason") or "").strip().upper()
        status = str(row.get("status") or "").strip().upper()
        if reason.endswith("_LOCKED") or status == "PASS_LOCKED":
            approved_steps.append(
                {
                    "revision": int(row.get("revision") or 0),
                    "status": status,
                    "reason": reason,
                    "created_at": str(row.get("created_at") or ""),
                    "seed_text": str(row.get("seed_text") or ""),
                }
            )

    lines = [
        "# DERAX Project Audit",
        "",
        "# Canonical summary",
        "",
        "Approved DERAX trail and data snapshot.",
        "",
        "# Project",
        "",
        f"- project_id: {project.id}",
        f"- project_name: {project.name}",
        f"- workflow_mode: {project.workflow_mode}",
        f"- generated_at: {created}",
        "",
        "# WorkItem",
        "",
        f"- work_item_id: {work_item.id}",
        f"- title: {work_item.title}",
        f"- state: {work_item.state}",
        f"- active_phase: {work_item.active_phase}",
        f"- active_seed_revision: {work_item.active_seed_revision}",
        f"- active_seed_text: {active_seed_text}",
        "",
        "# Approved steps",
        "",
        _json_block(approved_steps),
        "",
        "# Seed log",
        "",
        _json_block(list(work_item.seed_log or [])),
        "",
        "# Activity log",
        "",
        _json_block(list(work_item.activity_log or [])),
        "",
        "# Deliverables",
        "",
        _json_block(list(work_item.deliverables or [])),
        "",
        "# DERAX runs",
        "",
        _json_block(list(work_item.derax_runs or [])),
        "",
        "# Define history",
        "",
        _json_block(list(work_item.derax_define_history or [])),
        "",
        "# Explore history",
        "",
        _json_block(list(work_item.derax_explore_history or [])),
        "",
        "# Endpoint spec",
        "",
        str(work_item.derax_endpoint_spec or ""),
        "",
        "# Snapshot JSON",
        "",
        _json_block(
            {
                "project": {
                    "id": int(project.id),
                    "name": str(project.name or ""),
                    "workflow_mode": str(project.workflow_mode or ""),
                },
                "work_item": {
                    "id": int(work_item.id),
                    "title": str(work_item.title or ""),
                    "intent_raw": str(work_item.intent_raw or ""),
                    "state": str(work_item.state or ""),
                    "active_phase": str(work_item.active_phase or ""),
                    "active_seed_revision": int(work_item.active_seed_revision or 0),
                    "seed_log": list(work_item.seed_log or []),
                    "activity_log": list(work_item.activity_log or []),
                    "deliverables": list(work_item.deliverables or []),
                    "derax_runs": list(work_item.derax_runs or []),
                    "derax_define_history": list(work_item.derax_define_history or []),
                    "derax_explore_history": list(work_item.derax_explore_history or []),
                    "derax_endpoint_spec": str(work_item.derax_endpoint_spec or ""),
                    "derax_endpoint_locked": bool(work_item.derax_endpoint_locked),
                },
            }
        ),
        "",
    ]
    return "\n".join(lines)


def persist_derax_project_audit(work_item: WorkItem, *, user) -> ProjectDocument:
    content = build_derax_project_audit_text(work_item)
    project = work_item.project
    stem = _safe_stem(project.name)
    filename = f"{stem}-DERAX-Audit.txt"
    rel_name = f"derax/{int(work_item.id)}/{filename}"
    doc = ProjectDocument(
        project=project,
        title=f"{project.name} DERAX Audit"[:200],
        original_name=filename[:255],
        content_type="text/plain",
        size_bytes=len(content.encode("utf-8")),
        uploaded_by=user,
    )
    doc.file.save(rel_name, ContentFile(content.encode("utf-8")), save=False)
    doc.save()
    work_item.add_deliverable(ref=str(doc.original_name or filename), note=f"doc_id={doc.id}", actor="system")
    work_item.append_activity(
        actor="system",
        action="derax_audit_generated",
        notes=f"asset_id={doc.id}",
    )
    return doc

