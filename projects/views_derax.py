# -*- coding: utf-8 -*-

from __future__ import annotations

from django.contrib import messages
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone

from projects.models import WorkItem
from projects.services_phase_output_validator import build_phase_correction_request, validate_phase_output
from projects.services.context_resolution import resolve_effective_context
from projects.services_project_membership import accessible_projects_qs
from chats.services.contracts.pipeline import ContractContext
from chats.services.contracts.phase_resolver import resolve_phase_contract
from chats.services.llm import generate_text


def _primary_work_item_for_project(project) -> WorkItem:
    work_item = (
        WorkItem.objects
        .filter(project=project, is_primary=True)
        .order_by("-updated_at", "-id")
        .first()
    )
    if work_item is not None:
        return work_item

    fallback = (
        WorkItem.objects
        .filter(project=project)
        .order_by("-updated_at", "-id")
        .first()
    )
    if fallback is not None:
        fallback.is_primary = True
        fallback.save(update_fields=["is_primary", "updated_at"])
        return fallback

    work_item = WorkItem.create_minimal(
        project=project,
        title=str(getattr(project, "name", "") or "")[:200],
        active_phase=WorkItem.PHASE_DEFINE,
    )
    work_item.is_primary = True
    work_item.save(update_fields=["is_primary", "updated_at"])
    return work_item


def _latest_define_assistant_text(work_item: WorkItem) -> str:
    history_entries = [h for h in list(work_item.derax_define_history or []) if isinstance(h, dict)]
    for row in reversed(history_entries):
        if str(row.get("role") or "").strip().lower() != "assistant":
            continue
        text = str(row.get("text") or "").strip()
        if text:
            return text
    return ""


@login_required
def derax_project_home(request, project_id: int):
    project = get_object_or_404(accessible_projects_qs(request.user), id=project_id)
    work_item = _primary_work_item_for_project(project)

    if request.method == "POST":
        action = str(request.POST.get("action") or "").strip().lower()
        if action in {"autosave_end_in_mind", "save_end_in_mind"}:
            end_in_mind = str(request.POST.get("end_in_mind") or "").strip()
            work_item.intent_raw = end_in_mind
            if end_in_mind and not str(work_item.title or "").strip():
                work_item.title = end_in_mind[:200]
            work_item.save(update_fields=["intent_raw", "title", "updated_at"])
            work_item.append_activity(
                actor=request.user,
                action="end_in_mind_saved",
                notes="DEFINE end-in-mind autosaved.",
            )
            if str(request.headers.get("x-requested-with") or "").lower() == "xmlhttprequest":
                return JsonResponse({"ok": True, "saved_text": end_in_mind})
            messages.success(request, "End in mind saved.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "use_define_response_as_intent":
            candidate = str(request.POST.get("candidate_text") or "").strip()
            if not candidate:
                candidate = _latest_define_assistant_text(work_item)
            if not candidate:
                messages.error(request, "No DEFINE response available to use as intent.")
                return redirect("projects:derax_project_home", project_id=project.id)
            work_item.intent_raw = candidate
            if not str(work_item.title or "").strip():
                work_item.title = candidate[:200]
            work_item.save(update_fields=["intent_raw", "title", "updated_at"])
            work_item.append_activity(
                actor=request.user,
                action="end_in_mind_from_define",
                notes="Intent updated from latest DEFINE response.",
            )
            messages.success(request, "DEFINE response applied to intent.")
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "define_llm_turn":
            user_input = str(request.POST.get("define_user_input") or "").strip()
            if not user_input:
                messages.error(request, "Enter text for the DEFINE LLM turn.")
                return redirect("projects:derax_project_home", project_id=project.id)

            history_entries = [h for h in list(work_item.derax_define_history or []) if isinstance(h, dict)]
            history_entries = history_entries[-40:]
            messages_list = []
            for row in history_entries:
                role = str(row.get("role") or "").strip().lower()
                if role not in {"user", "assistant"}:
                    continue
                text = str(row.get("text") or "").strip()
                if not text:
                    continue
                messages_list.append({"role": role, "content": text})
            messages_list.append({"role": "user", "content": user_input})

            effective_context = {}
            try:
                effective_context = dict(
                    resolve_effective_context(
                        project_id=project.id,
                        user_id=request.user.id,
                        session_overrides={},
                        chat_overrides={},
                    )
                    or {}
                )
            except Exception:
                effective_context = {}

            original_phase = str(work_item.active_phase or "")
            try:
                work_item.active_phase = WorkItem.PHASE_DEFINE
                contract_ctx = ContractContext(
                    user=request.user,
                    project=project,
                    work_item=work_item,
                    active_phase=WorkItem.PHASE_DEFINE,
                    user_text=user_input,
                    effective_context=effective_context,
                    legacy_system_blocks=[
                        "DEFINE TURN MODE: Focus only on defining end-in-mind intent. "
                        "Do not produce plans, architecture, implementation steps, or delivery sequencing."
                    ],
                    include_envelope=False,
                    strict_json=False,
                )
                llm_text = generate_text(
                    system_blocks=[],
                    messages=messages_list,
                    user=request.user,
                    contract_ctx=contract_ctx,
                )
                ok, missing = validate_phase_output(work_item=work_item, text=str(llm_text or ""))
                if not ok:
                    correction = build_phase_correction_request(
                        missing_headers=list(missing or []),
                        draft_text=str(llm_text or ""),
                    )
                    llm_text = generate_text(
                        system_blocks=[],
                        messages=messages_list + [
                            {"role": "assistant", "content": str(llm_text or "")},
                            {"role": "user", "content": correction},
                        ],
                        user=request.user,
                        contract_ctx=contract_ctx,
                    )
            except Exception as exc:
                messages.error(request, f"DEFINE LLM turn failed: {exc}")
                work_item.active_phase = original_phase
                return redirect("projects:derax_project_home", project_id=project.id)
            finally:
                work_item.active_phase = original_phase

            now_iso = timezone.now().isoformat()
            history_entries.append({"role": "user", "text": user_input, "timestamp": now_iso})
            history_entries.append({"role": "assistant", "text": str(llm_text or "").strip(), "timestamp": now_iso})
            history_entries = history_entries[-40:]
            work_item.derax_define_history = history_entries
            work_item.save(update_fields=["derax_define_history", "updated_at"])
            work_item.append_activity(
                actor=request.user,
                action="define_llm_turn",
                notes="DEFINE turn recorded.",
            )
            if str(request.headers.get("x-requested-with") or "").lower() == "xmlhttprequest":
                define_history = list(work_item.derax_define_history or [])
                latest_text = _latest_define_assistant_text(work_item)
                history_html = render_to_string(
                    "projects/_derax_define_history.html",
                    {
                        "define_history": define_history,
                    },
                    request=request,
                )
                return JsonResponse(
                    {
                        "ok": True,
                        "history_html": history_html,
                        "latest_define_assistant_text": latest_text,
                    }
                )
            return redirect("projects:derax_project_home", project_id=project.id)

        if action == "lock_define_and_explore":
            intent = str(work_item.intent_raw or "").strip()
            if not intent:
                messages.error(request, "Set intent first before locking DEFINE.")
                return redirect("projects:derax_project_home", project_id=project.id)
            if str(work_item.active_phase or "").strip().upper() != WorkItem.PHASE_DEFINE:
                messages.error(request, "DEFINE can only be locked while active phase is DEFINE.")
                return redirect("projects:derax_project_home", project_id=project.id)
            try:
                work_item.append_seed_revision(
                    seed_text=intent,
                    created_by=request.user,
                    reason="DEFINE_LOCKED",
                )
                work_item.set_phase(WorkItem.PHASE_EXPLORE)
            except Exception as exc:
                messages.error(request, str(exc))
                return redirect("projects:derax_project_home", project_id=project.id)
            messages.success(request, "DEFINE locked to history. Phase moved to EXPLORE.")
            return redirect("projects:derax_project_home", project_id=project.id)

    seed_history = []
    for item in reversed(list(work_item.seed_log or [])):
        if not isinstance(item, dict):
            continue
        seed_history.append(
            {
                "revision": int(item.get("revision") or 0),
                "status": str(item.get("status") or ""),
                "created_at": str(item.get("created_at") or ""),
                "reason": str(item.get("reason") or ""),
                "seed_text": str(item.get("seed_text") or ""),
            }
        )

    return render(
        request,
        "projects/derax_home.html",
        {
            "project": project,
            "work_item": work_item,
            "seed_history": seed_history,
            "define_history": list(work_item.derax_define_history or []),
            "latest_define_assistant_text": _latest_define_assistant_text(work_item),
            "define_help_text": (
                "Describe the outcome you want from this DERAX process. "
                "State what good looks like. "
                "Write it in your own words so the LLM can help define and refine it."
            ),
            "show_contract_debug": bool(settings.DEBUG),
            "active_phase_contract": resolve_phase_contract(
                ContractContext(
                    user=request.user,
                    project=project,
                    work_item=work_item,
                    active_phase=str(work_item.active_phase or ""),
                    user_text="",
                    include_envelope=False,
                    strict_json=False,
                )
            ),
        },
    )
