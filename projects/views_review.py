from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from chats.models import ChatWorkspace
from chats.services.turns import build_chat_turn_context
from projects.models import ProjectAnchor, ProjectReviewChat
from projects.services_project_membership import accessible_projects_qs
from projects.services_review_chat import get_or_create_review_chat

MARKERS = [
    ("INTENT", "Intent", "CKO (text)"),
    ("ROUTE", "Route", "PDO (JSON/text)"),
    ("EXECUTE", "Execute", "Execution state (text/JSON)"),
    ("COMPLETE", "Complete", "Completion report (text)"),
]


@login_required
def project_review(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    request.session["rw_active_project_id"] = project.id
    request.session.modified = True

    anchors = {a.marker: a for a in ProjectAnchor.objects.filter(project=project)}
    chats = {
        rc.marker: rc.chat_id
        for rc in ProjectReviewChat.objects.filter(project=project, user=request.user)
    }

    review_chat_id_raw = (request.GET.get("review_chat_id") or "").strip()
    open_param = (request.GET.get("review_chat_open") or "").strip()
    selected_chat_id = int(review_chat_id_raw) if review_chat_id_raw.isdigit() else None

    chat_ids = set(chats.values())
    chat_ctx_map = {}
    if chat_ids:
        chat_objs = {c.id: c for c in ChatWorkspace.objects.filter(id__in=chat_ids)}
        for chat_id, chat in chat_objs.items():
            ctx = build_chat_turn_context(request, chat)
            qs = request.GET.copy()
            qs["review_chat_id"] = str(chat.id)
            qs["review_chat_open"] = "1"
            qs["system"] = "1"
            qs.pop("turn", None)
            ctx["chat"] = chat
            ctx["qs_base"] = qs.urlencode()
            if selected_chat_id == chat.id and open_param in ("0", "1"):
                ctx["is_open"] = (open_param == "1")
            else:
                ctx["is_open"] = False
            chat_ctx_map[chat.id] = ctx

    sections = []
    for marker, label, anchor_type in MARKERS:
        anchor = anchors.get(marker)
        sections.append(
            {
                "marker": marker,
                "label": label,
                "anchor_type": anchor_type,
                "status": anchor.status if anchor else "DRAFT",
                "content": (anchor.content or "") if anchor else "",
                "chat_id": chats.get(marker),
                "chat_ctx": chat_ctx_map.get(chats.get(marker)),
            }
        )

    return render(
        request,
        "projects/project_review.html",
        {
            "project": project,
            "sections": sections,
        },
    )


@require_POST
@login_required
def project_review_chat_open(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), pk=project_id)
    marker = (request.POST.get("marker") or "").strip().upper()
    markers = {m[0] for m in MARKERS}
    if marker not in markers:
        messages.error(request, "Unknown review marker.")
        return redirect("projects:project_review", project_id=project.id)

    seed = (
        "You are helping review and refine the "
        + marker
        + " for this project.\n"
        + "Your role is to:\n"
        + "- clarify\n"
        + "- ask questions\n"
        + "- propose improvements\n"
        + "- help produce a stable version suitable for acceptance."
    )
    chat = get_or_create_review_chat(
        project=project,
        user=request.user,
        marker=marker,
        seed_text=seed,
        session_overrides=request.session.get("rw_session_overrides", {}) or {},
    )
    return redirect(
        reverse("projects:project_review", args=[project.id])
        + "?review_chat_id="
        + str(chat.id)
        + "&review_chat_open=1#review-"
        + marker.lower()
    )
