from __future__ import annotations

from projects.models import Project, ProjectAnchor
from projects.services_artefacts import seed_execute_from_route as build_execute_seed, merge_execute_payload


def seed_execute_from_route(project: Project) -> ProjectAnchor | None:
    route = ProjectAnchor.objects.filter(project=project, marker="ROUTE").first()
    if not route or not isinstance(route.content_json, dict) or not route.content_json:
        return None
    exec_anchor = ProjectAnchor.objects.filter(project=project, marker="EXECUTE").first()
    if exec_anchor and (exec_anchor.content_json or exec_anchor.content):
        return exec_anchor
    payload = build_execute_seed(route.content_json)
    if exec_anchor:
        exec_anchor.content_json = payload
        exec_anchor.content = ""
        exec_anchor.save(update_fields=["content_json", "content", "updated_at"])
        return exec_anchor
    return ProjectAnchor.objects.create(
        project=project,
        marker="EXECUTE",
        content_json=payload,
        content="",
        status=ProjectAnchor.Status.DRAFT,
    )


def merge_execute_from_route(project: Project) -> ProjectAnchor | None:
    route = ProjectAnchor.objects.filter(project=project, marker="ROUTE").first()
    if not route or not isinstance(route.content_json, dict) or not route.content_json:
        return None
    exec_anchor = ProjectAnchor.objects.filter(project=project, marker="EXECUTE").first()
    if not exec_anchor:
        exec_anchor = ProjectAnchor.objects.create(
            project=project,
            marker="EXECUTE",
        content_json=build_execute_seed(route.content_json),
            content="",
            status=ProjectAnchor.Status.DRAFT,
        )
        return exec_anchor
    merged = merge_execute_payload(exec_anchor.content_json or {}, build_execute_seed(route.content_json))
    exec_anchor.content_json = merged
    exec_anchor.content = ""
    exec_anchor.save(update_fields=["content_json", "content", "updated_at"])
    return exec_anchor


def reseed_execute_from_route(project: Project) -> ProjectAnchor | None:
    route = ProjectAnchor.objects.filter(project=project, marker="ROUTE").first()
    if not route or not isinstance(route.content_json, dict) or not route.content_json:
        return None
    payload = build_execute_seed(route.content_json)
    exec_anchor = ProjectAnchor.objects.filter(project=project, marker="EXECUTE").first()
    if not exec_anchor:
        return ProjectAnchor.objects.create(
            project=project,
            marker="EXECUTE",
            content_json=payload,
            content="",
            status=ProjectAnchor.Status.DRAFT,
        )
    exec_anchor.content_json = payload
    exec_anchor.content = ""
    exec_anchor.save(update_fields=["content_json", "content", "updated_at"])
    return exec_anchor
