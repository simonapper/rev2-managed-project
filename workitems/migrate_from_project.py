# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _setup_django() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))
    if not os.getenv("DJANGO_SETTINGS_MODULE"):
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "workbench.settings")
    import django

    django.setup()


def _extract_intent_raw(project) -> str:
    cko = getattr(project, "defined_cko", None)
    if cko is not None:
        text = str(getattr(cko, "content_text", "") or "").strip()
        if text:
            return text
        as_json = getattr(cko, "content_json", None) or {}
        if as_json:
            return json.dumps(as_json, ensure_ascii=True, indent=2)

    ppde_seed = getattr(project, "ppde_seed_summary", None) or {}
    if ppde_seed:
        return json.dumps(ppde_seed, ensure_ascii=True, indent=2)

    return "No legacy intent content found. Capture intent in first revision."


def _find_locked_anchor(project):
    from projects.models import ProjectAnchor

    marker_order = ["INTENT", "ROUTE", "EXECUTE", "COMPLETE"]
    qs = ProjectAnchor.objects.filter(project=project).order_by("id")
    for marker in marker_order:
        row = qs.filter(marker=marker, status=ProjectAnchor.Status.PASS_LOCKED).first()
        if row is not None:
            return row
    return None


def _anchor_seed_text(anchor) -> str:
    if anchor is None:
        return ""
    text = str(getattr(anchor, "content", "") or "").strip()
    if text:
        return text
    payload = getattr(anchor, "content_json", None) or {}
    if payload:
        return json.dumps(payload, ensure_ascii=True, indent=2)
    return ""


def migrate_from_project(project_id: int, *, dry_run: bool = True) -> dict:
    from projects.models import Project, WorkItem

    project = Project.objects.filter(id=int(project_id)).select_related("defined_cko").first()
    if project is None:
        raise ValueError(f"Project not found: {project_id}")

    title = (str(getattr(project, "name", "") or "").strip() or f"Project {project.id}")[:200]
    intent_raw = _extract_intent_raw(project)
    locked_anchor = _find_locked_anchor(project)
    locked_seed = _anchor_seed_text(locked_anchor)

    plan = {
        "project_id": project.id,
        "work_item_title": title,
        "intent_raw_preview": (intent_raw[:200] + "...") if len(intent_raw) > 200 else intent_raw,
        "has_locked_anchor": bool(locked_anchor),
        "locked_anchor_marker": getattr(locked_anchor, "marker", "") if locked_anchor else "",
    }

    if dry_run:
        print("[DRY RUN] WorkItem migration plan")
        print(json.dumps(plan, ensure_ascii=True, indent=2))
        return plan

    work_item = WorkItem.create_minimal(
        project=project,
        active_phase=WorkItem.PHASE_SEED,
        title=title,
        intent_raw=intent_raw,
    )
    work_item.append_activity(
        actor="system",
        action="migrate_from_project",
        notes=f"project_id={project.id}",
    )

    if locked_seed:
        work_item.append_seed_revision(
            seed_text=locked_seed,
            created_by=(getattr(project, "owner", None)),
            reason=f"Migrated from locked {getattr(locked_anchor, 'marker', 'anchor')}",
        )
        work_item.lock_seed(work_item.active_seed_revision)
        work_item.append_activity(
            actor="system",
            action="migrate_seed_locked",
            notes=f"marker={getattr(locked_anchor, 'marker', '')}",
        )

    result = {
        "project_id": project.id,
        "work_item_id": work_item.id,
        "title": work_item.title,
        "active_phase": work_item.active_phase,
        "active_seed_revision": work_item.active_seed_revision,
    }
    print("[APPLIED] WorkItem created")
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap a WorkItem from legacy Project data.")
    parser.add_argument("project_id", type=int, help="Project ID to migrate from")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    args = parser.parse_args(argv)

    _setup_django()
    migrate_from_project(args.project_id, dry_run=not args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
