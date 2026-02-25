# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
from typing import Any

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.utils import timezone

from projects.models import Project, ProjectDocument, WorkItem


def _safe_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._-") or "derax"


def _resolve_uploaded_by(*, project: Project, user_id: int | None = None):
    if user_id:
        user_model = get_user_model()
        user = user_model.objects.filter(id=int(user_id)).first()
        if user is not None:
            return user
    return project.owner


def _persist_payload_doc(
    *,
    project: Project,
    chat_id: int,
    turn_id: str,
    phase: str,
    payload: dict,
    user_id: int | None = None,
) -> ProjectDocument:
    phase_text = str(phase or "").strip().upper() or "DEFINE"
    turn_stem = _safe_stem(turn_id)[:80]
    rel_name = f"derax/{int(chat_id)}/{turn_stem}_{phase_text}.json"
    body = json.dumps(payload or {}, ensure_ascii=True, indent=2)
    uploaded_by = _resolve_uploaded_by(project=project, user_id=user_id)
    doc = ProjectDocument(
        project=project,
        title=f"DERAX JSON {phase_text} {turn_stem}"[:200],
        original_name=rel_name[:255],
        content_type="application/json",
        size_bytes=len(body.encode("utf-8")),
        uploaded_by=uploaded_by,
    )
    doc.file.save(rel_name, ContentFile(body.encode("utf-8")), save=False)
    doc.save()
    return doc


def _append_work_item_derax_run(*, work_item: WorkItem, phase: str, doc: ProjectDocument, turn_id: str) -> None:
    runs = list(getattr(work_item, "derax_runs", []) or [])
    runs.append(
        {
            "phase": str(phase or "").strip().upper(),
            "asset_id": int(doc.id),
            "created_at": timezone.now().isoformat(),
            "path": str(doc.file.name or ""),
            "turn_id": str(turn_id or "").strip(),
        }
    )
    work_item.derax_runs = runs
    work_item.save(update_fields=["derax_runs", "updated_at"])


def persist_derax_payload(
    *,
    project_id: int | None = None,
    chat_id: int | None = None,
    turn_id: str = "",
    phase: str = "",
    payload: dict | None = None,
    raw_text: str = "",
    user_id: int | None = None,
    work_item: WorkItem | None = None,
    user: Any = None,
    chat: Any = None,
) -> str | ProjectDocument:
    del raw_text, chat
    payload_dict = dict(payload or {})

    # Backward-compatible path used by existing views/tests.
    if work_item is not None:
        project = work_item.project
        resolved_chat_id = int(chat_id or 0)
        if resolved_chat_id <= 0:
            meta = payload_dict.get("meta", {})
            try:
                resolved_chat_id = int((meta or {}).get("chat_id") or 0)
            except Exception:
                resolved_chat_id = 0
        if resolved_chat_id <= 0:
            resolved_chat_id = int(work_item.id)
        resolved_turn_id = str(turn_id or timezone.now().strftime("%Y%m%dT%H%M%S")).strip()
        resolved_phase = str(phase or payload_dict.get("phase") or work_item.active_phase or "DEFINE").strip().upper()
        resolved_user_id = int(getattr(user, "id", 0) or user_id or 0) or None
        doc = _persist_payload_doc(
            project=project,
            chat_id=resolved_chat_id,
            turn_id=resolved_turn_id,
            phase=resolved_phase,
            payload=payload_dict,
            user_id=resolved_user_id,
        )
        _append_work_item_derax_run(work_item=work_item, phase=resolved_phase, doc=doc, turn_id=resolved_turn_id)
        return doc

    if project_id is None or chat_id is None:
        raise ValueError("project_id and chat_id are required when work_item is not provided.")

    project = Project.objects.filter(id=int(project_id)).first()
    if project is None:
        raise ValueError("Project not found.")

    resolved_turn_id = str(turn_id or timezone.now().strftime("%Y%m%dT%H%M%S")).strip()
    resolved_phase = str(phase or payload_dict.get("meta", {}).get("phase") or payload_dict.get("phase") or "DEFINE").strip().upper()
    doc = _persist_payload_doc(
        project=project,
        chat_id=int(chat_id),
        turn_id=resolved_turn_id,
        phase=resolved_phase,
        payload=payload_dict,
        user_id=user_id,
    )
    return str(doc.id)

