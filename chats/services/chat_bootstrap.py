# chats/services/chat_bootstrap.py
# Patch: return (chat, cde_result) so UI can render validation feedback.

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from chats.models import ChatWorkspace, ChatMessage
from chats.services.cde_loop import run_cde
from chats.services.cde_injection import build_cde_system_blocks
from projects.models import Project, ProjectPlanningPurpose, ProjectPlanningStage


# NOTE: keep existing imports and existing logic you already have for:
# - resolve_effective_context
# - system block persistence
# - handshake generation
# Only the CDE handling + return shape changes below.


def bootstrap_chat(
    *,
    project,
    user,
    title: str,
    generate_panes_func,
    session_overrides: Optional[Dict[str, Any]] = None,
    cde_mode: str = "SKIP",  # "SKIP"|"LOOSE"|"CONTROLLED"
    cde_inputs: Optional[Dict[str, str]] = None,
    skip_readiness_checks: bool = False,
) -> Tuple[ChatWorkspace, Dict[str, Any]]:
    session_overrides = session_overrides or {}
    cde_inputs = cde_inputs or {}
    
    if not skip_readiness_checks:
        if project.defined_cko_id is None and project.kind != Project.Kind.SANDBOX:
            raise ValueError("Project is not initialised (no accepted CKO).")
        if project.kind != Project.Kind.SANDBOX:
            ppde_started = (
                ProjectPlanningPurpose.objects.filter(project=project).exists()
                or ProjectPlanningStage.objects.filter(project=project).exists()
            )
            if not ppde_started:
                raise ValueError("PPDE has not been started.")



    # ------------------------------------------------------------
    # Create chat row
    # ------------------------------------------------------------
    chat = ChatWorkspace.objects.create(
        project=project,
        created_by=user,
        title=title,
    )

    # ------------------------------------------------------------
    # Run CDE (optional)
    # ------------------------------------------------------------
    cde_mode_u = (cde_mode or "SKIP").strip().upper()
    cde_result: Dict[str, Any] = {
        "ok": True,
        "mode": cde_mode_u,
        "locked": False,
        "results": [],
        "first_blocker": None,
    }

    if cde_mode_u == "LOOSE":
        def _generate_panes_for_user(*args, **kwargs):
            kwargs.setdefault("user", user)
            return generate_panes_func(*args, **kwargs)

        cde_result = run_cde(
            chat=chat,
            generate_panes_func=_generate_panes_for_user,
            user_inputs=cde_inputs,
            mode="LOOSE",
            save_loose_partials=True,
        )
    elif cde_mode_u == "CONTROLLED":
        def _generate_panes_for_user(*args, **kwargs):
            kwargs.setdefault("user", user)
            return generate_panes_func(*args, **kwargs)

        cde_result = run_cde(
            chat=chat,
            generate_panes_func=_generate_panes_for_user,
            user_inputs=cde_inputs,
            mode="CONTROLLED",
            save_loose_partials=True,
        )

    # ------------------------------------------------------------
    # Build boot-time SYSTEM blocks
    # NOTE: extend this list with your other system blocks as needed
    # ------------------------------------------------------------
    system_blocks: List[str] = []
    system_blocks.extend(build_cde_system_blocks(chat))

    # ------------------------------------------------------------
    # Persist boot-time SYSTEM message for traceability
    # ------------------------------------------------------------
  
    return chat, cde_result
