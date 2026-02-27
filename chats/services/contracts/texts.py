# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any

from django.db.models import Q

from chats.models import ContractText
from chats.services.contracts.lint import lint_contract_text


SUPPORTED_CONTRACT_TEXT_KEYS = (
    "language",
    "tone",
    "reasoning",
    "approach",
    "control",
    "boundary.profile",
    "active_avatars",
    "phase.contract",
    "cde.contract",
    "phase.define",
    "phase.explore",
    "phase.refine",
    "phase.approve",
    "phase.execute",
    "phase.complete",
)

CONTRACT_TEXT_LABELS = {
    "language": "Language protocol",
    "tone": "Tone protocol",
    "reasoning": "Reasoning protocol",
    "approach": "Approach protocol",
    "control": "Control protocol",
    "boundary.profile": "Boundary profile",
    "active_avatars": "Active avatars summary",
    "phase.contract": "Effective phase contract",
    "cde.contract": "CDE contract",
    "pde.validator.boilerplate": "PDE validator boilerplate",
    "pde.draft.boilerplate": "PDE draft boilerplate",
    "cde.validator.boilerplate": "CDE validator boilerplate",
    "cde.draft.boilerplate": "CDE draft boilerplate",
    "cko.review.system_block": "CKO review system block",
    "phase.define": "Phase contract: DEFINE",
    "phase.explore": "Phase contract: EXPLORE",
    "phase.refine": "Phase contract: REFINE",
    "phase.approve": "Phase contract: APPROVE",
    "phase.execute": "Phase contract: EXECUTE",
    "phase.complete": "Phase contract: COMPLETE",
}


_BLOCK_TO_TEXT_KEY = {
    "avatars.protocol.0": "language",
    "avatars.protocol.1": "tone",
    "avatars.protocol.2": "reasoning",
    "avatars.protocol.3": "approach",
    "avatars.protocol.4": "control",
    "boundary.effective": "boundary.profile",
    "avatars.protocol.5": "active_avatars",
    "cde.contract.0": "cde.contract",
    "phase.define": "phase.define",
    "phase.explore": "phase.explore",
    "phase.refine": "phase.refine",
    "phase.approve": "phase.approve",
    "phase.execute": "phase.execute",
    "phase.complete": "phase.complete",
}


def is_supported_contract_text_key(key: str) -> bool:
    return str(key or "").strip().lower() in SUPPORTED_CONTRACT_TEXT_KEYS


def normalise_contract_text_key(key: str) -> str:
    out = str(key or "").strip().lower()
    if not is_supported_contract_text_key(out):
        raise ValueError("Unsupported contract text key.")
    return out


def resolve_phase_key_from_context(ctx: Any | None = None) -> str | None:
    if ctx is None:
        return None
    work_item = getattr(ctx, "work_item", None)
    phase = str(getattr(work_item, "active_phase", "") or getattr(ctx, "active_phase", "") or "").strip().upper()
    if not phase:
        return None
    candidate = f"phase.{phase.lower()}"
    if is_supported_contract_text_key(candidate):
        return candidate
    return None


def map_block_key_to_contract_text_key(block_key: str, *, ctx: Any | None = None) -> str | None:
    key = str(block_key or "").strip().lower()
    if key == "phase.contract":
        return resolve_phase_key_from_context(ctx)
    return _BLOCK_TO_TEXT_KEY.get(key)


def _active_row(
    *,
    key: str,
    scope_type: str,
    scope_id: int | None,
    scope_project_id: int | None = None,
    scope_user_id: int | None = None,
) -> ContractText | None:
    qs = ContractText.objects.filter(
        key=key,
        scope_type=scope_type,
        status=ContractText.Status.ACTIVE,
    )
    if scope_type == ContractText.ScopeType.GLOBAL_DEFAULT:
        qs = qs.filter(scope_id__isnull=True, scope_project__isnull=True, scope_user__isnull=True)
    elif scope_type == ContractText.ScopeType.USER:
        candidate_user_id = int(scope_user_id or scope_id or 0)
        if candidate_user_id > 0:
            qs = qs.filter(Q(scope_user_id=candidate_user_id) | Q(scope_id=candidate_user_id))
        else:
            qs = qs.filter(scope_user__isnull=True, scope_id__isnull=True)
    elif scope_type == ContractText.ScopeType.PROJECT:
        qs = qs.filter(scope_project_id=scope_project_id)
    elif scope_type == ContractText.ScopeType.PROJECT_USER:
        qs = qs.filter(scope_project_id=scope_project_id, scope_user_id=scope_user_id)
    else:
        qs = qs.filter(scope_id=scope_id)
    qs = qs.order_by("-updated_at", "-id")
    return qs.first()


def resolve_contract_text(user, key: str, *, project_id: int | None = None) -> dict:
    contract_key = normalise_contract_text_key(key)
    user_id = int(getattr(user, "id", 0) or 0)
    project_id_int = int(project_id or 0)

    default_row = _active_row(
        key=contract_key,
        scope_type=ContractText.ScopeType.GLOBAL_DEFAULT,
        scope_id=None,
    )
    project_row = None
    if project_id_int > 0:
        project_row = _active_row(
            key=contract_key,
            scope_type=ContractText.ScopeType.PROJECT,
            scope_id=None,
            scope_project_id=project_id_int,
        )

    user_row = None
    if user_id > 0:
        user_row = _active_row(
            key=contract_key,
            scope_type=ContractText.ScopeType.USER,
            scope_id=user_id,
            scope_user_id=user_id,
        )
    project_user_row = None
    if project_id_int > 0 and user_id > 0:
        project_user_row = _active_row(
            key=contract_key,
            scope_type=ContractText.ScopeType.PROJECT_USER,
            scope_id=None,
            scope_project_id=project_id_int,
            scope_user_id=user_id,
        )

    def _row_ok(row: ContractText | None) -> bool:
        if row is None:
            return False
        lint = lint_contract_text(key=contract_key, text=str(row.text or ""))
        return bool(lint.get("ok"))

    default_text = str(default_row.text or "") if default_row is not None else ""
    project_text = str(project_row.text or "") if project_row is not None else None
    user_text = str(user_row.text or "") if user_row is not None else None
    project_user_text = str(project_user_row.text or "") if project_user_row is not None else None

    if project_user_row is not None and _row_ok(project_user_row):
        return {
            "key": contract_key,
            "default_text": default_text,
            "project_text": project_text,
            "user_text": user_text,
            "project_user_text": project_user_text,
            "effective_text": str(project_user_row.text or ""),
            "effective_source": "PROJECT_USER",
        }

    if project_row is not None and _row_ok(project_row):
        return {
            "key": contract_key,
            "default_text": default_text,
            "project_text": project_text,
            "user_text": user_text,
            "project_user_text": project_user_text,
            "effective_text": str(project_row.text or ""),
            "effective_source": "PROJECT",
        }

    if user_row is not None and _row_ok(user_row):
        return {
            "key": contract_key,
            "default_text": default_text,
            "project_text": project_text,
            "user_text": user_text,
            "project_user_text": project_user_text,
            "effective_text": str(user_row.text or ""),
            "effective_source": "USER",
        }

    default_effective_text = default_text
    default_effective_source = "DEFAULT"
    if default_row is not None and not _row_ok(default_row):
        default_effective_text = ""
        default_effective_source = "DEFAULT_INVALID"

    return {
        "key": contract_key,
        "default_text": default_text,
        "project_text": project_text,
        "user_text": None,
        "project_user_text": project_user_text,
        "effective_text": default_effective_text,
        "effective_source": default_effective_source,
    }
