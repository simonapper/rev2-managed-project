# -*- coding: utf-8 -*-

from __future__ import annotations

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from projects.models import WorkItem


def _has_locked_seed(work_item: WorkItem) -> bool:
    for item in list(work_item.seed_log or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "") == WorkItem.SEED_STATUS_PASS_LOCKED:
            return True
    return False


def _key_decisions_from_seed_log(work_item: WorkItem) -> list[str]:
    decisions: list[str] = []
    for item in list(work_item.seed_log or []):
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or "").strip()
        if reason and reason not in decisions:
            decisions.append(reason)
    return decisions[:10]


def finalise_work_item(work_item: WorkItem) -> str:
    if work_item is None:
        raise ValueError("work_item is required.")
    if not _has_locked_seed(work_item):
        raise ValueError("Cannot finalise without PASS_LOCKED seed revision.")

    current_deliverables = [str(d).strip() for d in list(work_item.deliverables or []) if str(d).strip()]
    if not current_deliverables:
        raise ValueError("Cannot finalise without at least one deliverable.")

    rollback_point = int(work_item.active_seed_revision or 0)
    decisions = _key_decisions_from_seed_log(work_item)

    lines = [
        "# Final summary (brief)",
        "",
        "WorkItem completed with locked seed and recorded deliverables.",
        "",
        "# Artefact index (list of deliverables)",
        "",
    ]
    for item in current_deliverables:
        lines.append("- " + item)

    lines.extend(
        [
            "",
            "# Rollback point (active seed revision number)",
            "",
            str(rollback_point),
            "",
            "# Key decisions (from seed_log reasons, brief)",
            "",
        ]
    )
    if decisions:
        for reason in decisions:
            lines.append("- " + reason)
    else:
        lines.append("- (none)")
    lines.append("")
    markdown = "\n".join(lines)

    path = f"projects/{work_item.project_id}/workitems/{work_item.id}/FINAL_SUMMARY.md"
    default_storage.save(path, ContentFile(markdown.encode("utf-8")))
    work_item.add_deliverable(path, note="Final summary artefact")

    work_item.state = "COMPLETE"
    work_item.active_phase = WorkItem.PHASE_COMPLETE
    work_item.save(update_fields=["state", "active_phase", "updated_at"])
    work_item.append_activity(
        actor="system",
        action="work_item_finalised",
        notes=f"rollback_point={rollback_point}",
    )
    return markdown
