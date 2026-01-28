# -*- coding: utf-8 -*-
# config/management/commands/seed_system_configs.py
"""
Seed ORG-scoped system default configs for Sandbox slice (Levels 2 + 4 only)
and set active pointers.

Purpose
- Create out-of-the-box ORG "system files" for L2 and L4 as ConfigRecord + ConfigVersion rows.
- Set SystemConfigPointers (id=1) to point at those ORG defaults.
- Idempotent: safe to run multiple times (will not duplicate).

Usage
  python manage.py seed_system_configs
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from config.models import ConfigRecord, ConfigScope, ConfigVersion, SystemConfigPointers
from django.contrib.auth.models import AbstractUser



UserModel = get_user_model()


# FINAL APPROVED TEXTS (verbatim)
DEFAULT_TEXT_BY_LEVEL: dict[int, str] = {
    2: """# ============================================================
# FILE NAME: Level 2 llm_model_settings.conf
# LEVEL 2 SETTINGS - MODEL RISK & BEHAVIOUR ASSUMPTIONS
# PURPOSE:
# Define system-level assumptions, tolerances, and safeguards
# when interacting with LLMs.
#
# NOTE:
# - This file is NOT a direct instruction to the LLM.
# - It is consumed by the Navigator, UI, and enforcement layers.
#
# SCOPE:
# Level 2 - Model Behaviour Settings
# ============================================================


[1] Confidence & Trust Posture

- Default trust level in model outputs: LOW
- Model outputs require independent validation: ON
- Fluency treated as evidence of correctness: OFF
- Confidence treated as evidence of correctness: OFF


[2] Reasoning & Explanation Expectations

# NOTE:
# - Level 2 defines allowable reasoning modes and their epistemic risks.
# - Level 4 selects which mode is active for a given context.

- Require explicit assumptions when reasoning present: ON
- Allow implicit assumptions without flagging: OFF

- Reasoning visibility modes (enum):
  - Hidden
  - Summary
  - Full
  Default: Summary

- Post-hoc rationalisation risk assumed: ON


[3] Convergence & Exploration Controls

- Early convergence tolerance: LOW
- Require alternatives during exploratory stages: ON
- Single-narrative collapse allowed only in:
  - Decision stages
- Preserve alternatives until evaluation: ON


[4] Drift Detection Thresholds

Drift indicators monitored:
- Repetition without progress
- Increased verbosity without information gain
- Inconsistency with stated assumptions
- Loss of scope boundaries

Drift response recommendations:
- First detection: WARN user
- Repeated detection: SUGGEST checkpoint
- Persistent detection: RECOMMEND new chat


[5] Engineering Output Expectations

- Code correctness assumed without tests: OFF
- Tests expected for "done" state: ON
- "No tests" requires explicit exception: ON
- Environment declaration required: ON
- Dependency declaration required: ON


[6] Hallucination Risk Handling

- Assume API/library hallucination risk: HIGH
- Require verification flags for:
  - APIs
  - Flags
  - Versions
- Allow unverified claims without warning: OFF


[7] Multi-Model Awareness Assumptions

- Assume models are unaware of each other: ON
- Assume cross-context consistency: OFF
- Require explicit recomposition by Navigator: ON


[8] Data Sensitivity Assumptions

- Assume LLM retention behaviour unknown: ON
- Assume training exclusion cannot be guaranteed: ON
- Treat all external APIs as untrusted by default: ON


[9] Recovery & Off-Ramp Policy

- Allow silent continuation after instability: OFF
- Suggest checkpoint on instability: ON
- Allow user to override recovery suggestions: ON


[10] Scope of Authority

- Level 2 settings may:
  - trigger warnings
  - suggest actions
  - adjust defaults

- Level 2 settings may NOT:
  - block actions outright
  - override Level 3 policy
  - enforce routing or compartmentation


[11] Change Control

- Editable by: System Admins
- Changes logged and versioned: REQUIRED
- Applies globally unless scoped by Level 4


# ============================================================
# END OF LEVEL 2 SETTINGS
# ============================================================

# ============================================================
# LEVEL 4 CONTEXT - WORKING CONTEXT
# Purpose:
# Define delivery behaviour, interaction modes, and defaults
# for responses produced within a session or project.
# ============================================================


# ------------------------------------------------------------
# Language (Context Defaults)
# May be overridden explicitly per session.
# ------------------------------------------------------------
- default_language: English
- default_language_variant: British English
- active_language_code: en-GB
- language_switching_permitted: ON
- persist_language_switch_for_session_when_explicit: ON


# ============================================================
# LEVEL 4 AVATARS
# ============================================================

# - Cognitive Avatar Alternatives: Analyst | Explorer | Artist | Advocate
#   Default: Analyst


Cognitive Avatar Definitions:

Avatar: Analyst
COGNITIVE -ANALYST
- Structured, logic-first, fidelity-first.
- Use clear stage separation when helpful.

Avatar: Artist
COGNITIVE -ARTIST
- Creative synthesis; generate options and patterns.
- Use metaphor or analogy when helpful.
- Structure optional unless requested.

Avatar: Advocate
COGNITIVE -ADVOCATE
- Argue for the strongest recommended option.
- Surface key trade-offs and risks briefly.
- Persuasive but not manipulative.

Avatar: Explorer
COGNITIVE -EXPLORER
- Explore possibilities before converging.
- Preserve alternatives until decision requested.
- Ask clarifying questions when they change outcomes.


# - Epistemic Avatar Alternatives: Canonical | Analytical | Exploratory | Advocacy
#   Default: Canonical


Epistemic Avatar Definitions:

Avatar: Canonical
EPISTEMIC -CANONICAL
- Description precedes evaluation.
- Make assumptions explicit.
- Preserve alternatives until evaluation.
- Label uncertainty explicitly.
- State authority model when relevant.

Avatar: Analytical
EPISTEMIC -ANALYTICAL
- Evaluate claims systematically.
- Use explicit criteria where possible.
- Trade-offs made explicit.

Avatar: Exploratory
EPISTEMIC -EXPLORATORY
- Explore multiple hypotheses.
- Delay judgement until sufficient coverage.
- Highlight unknowns and uncertainties.

Avatar: Advocacy
EPISTEMIC -ADVOCACY
- Argue for a position once evidence is sufficient.
- Minimise alternative framing.
- State assumptions clearly.


# - Interaction Avatar Alternatives: Concise | Socratic | Didactic | Conversational
#   Default: Concise
#
# NOTE:
# "Reasoning available on request" means reasoning MUST be provided
# if explicitly requested, regardless of default visibility.


Interaction Avatar Definitions:

Avatar: Concise
INTERACTION - CONCISE
- Answer-first, then only essential detail.
- Keep it short; avoid padding and unnecessary framing.
- Offer reasoning only if asked.
- Use clear micro-structure when helpful (labels, short bullets).
- Push back firmly but respectfully when needed.

Avatar: Socratic
INTERACTION - SOCRATIC
- Guide via questions that change outcomes (not lots of trivia).
- Keep responses compact; prefer a single decisive next question.
- Share partial reasoning only as needed to frame questions.
- Use explicit transitions when shifting stages (e.g. clarify -> decide).
- Push back with curious, respectful probing.

Avatar: Didactic
INTERACTION - DIDACTIC
- Teach clearly: structured explanation with examples when useful.
- Show reasoning by default; explain the 'why', not just the 'what'.
- Keep precision high; define terms and assumptions when relevant.
- Use explicit transitions and signposting (overview -> steps -> checks).
- Correct errors neutrally and directly.

Avatar: Conversational
INTERACTION - CONVERSATIONAL
- Friendly, flexible tone; adapt to the user's style.
- Provide the answer, then expand only if it helps or is requested.
- Reasoning is optional: include lightly when it improves clarity.
- Warmth permitted; keep it human, not verbose.
- Push back gently and with empathy when needed.


# - Presentation Avatar Alternatives: Phone | Laptop | Tablet | Multi- Screen
#   Default: Laptop


Presentation Avatar Definitions:

Avatar: Phone
PRESENTATION - PHONE
- Ultra-short responses.
- Single-screen preference.
- No multi-column layouts.

Avatar: Laptop
PRESENTATION - LAPTOP
- Single-screen target (~35 lines).
- Answer-first.
- Reasoning on request.

Avatar: Tablet
PRESENTATION - TABLET
- Chunked sections preferred.
- Moderate scrolling allowed.
- Headings encouraged.

Avatar: Multi-Screen
PRESENTATION - MULTI-SCREEN
- Extended responses allowed.
- Multi-column layouts permitted.
- Reasoning visible by default.


# - Performance Avatar Alternatives: Focused | Balanced | Expansive
#   Default: Balanced


Performance Avatar Definitions:

Avatar: Focused
PERFORMANCE - FOCUSED
- Prefer shorter, bounded chats.
- High sensitivity to scope drift.
- Explicit context imports over implicit memory.

Avatar: Balanced
PERFORMANCE - BALANCED
- Balanced exploration and convergence.
- Moderate tolerance for scope drift.

Avatar: Expansive
PERFORMANCE - EXPANSIVE
- Long exploratory chats permitted.
- Low sensitivity to scope drift.


# - Checkpointing Avatar Alternatives: Manual | Assisted | Automatic
#   Default: Manual


Checkpointing Avatar Definitions:

Avatar: Manual
CHECKPOINTING - MANUAL
- No automatic checkpointing.
- Suggest checkpoint only at natural pauses.
- Export only on explicit user confirmation.

Avatar: Assisted
CHECKPOINTING - ASSISTED
- Suggest checkpoints gently when progress stalls.
- User confirmation required.

Avatar: Automatic
CHECKPOINTING - AUTOMATIC
- System proposes checkpoints automatically.
- User confirmation required for promotion.


# ============================================================
# END OF LEVEL 4 - WORKING CONTEXT
# ============================================================

""",
}


DEFAULT_CONFIGS: list[dict[str, object]] = [
    # Level 2 system defaults (ORG)
    {
        "level": 2,
        "file_id": "L2-SYSTEM-DEFAULTS",
        "file_name": "Level 2 System Defaults",
        "display_name": "L2 System Defaults",
    },
    # Level 4 system defaults (ORG)
    {
        "level": 4,
        "file_id": "L4-SYSTEM-DEFAULTS",
        "file_name": "Level 4 System Defaults",
        "display_name": "L4 System Defaults",
    },
]


def _get_or_create_org_scope() -> ConfigScope:
    scope, _ = ConfigScope.objects.get_or_create(
        scope_type=ConfigScope.ScopeType.ORG,
        project=None,
        user=None,
        session_id="",
    )
    return scope


def _get_seed_actor() -> AbstractUser | None:
    """
    Choose a sensible created_by actor.
    - Prefer first superuser.
    - Else any user.
    - Else None.
    """
    su = UserModel.objects.filter(is_superuser=True).order_by("id").first()
    if su:
        return su
    return UserModel.objects.order_by("id").first()


class Command(BaseCommand):
    help = "Seed ORG system default configs (L2 + L4 only) and set SystemConfigPointers"

    @transaction.atomic
    def handle(self, *args, **options):
        actor = _get_seed_actor()
        if actor is None:
            self.stderr.write(self.style.ERROR("No users exist. Create a superuser first."))
            return

        org_scope = _get_or_create_org_scope()

        created_records = 0
        created_versions = 0

        # Ensure singleton pointers row exists (do NOT touch L1/L3 pointers)
        pointers, _ = SystemConfigPointers.objects.get_or_create(pk=1)
        pointers.updated_by = actor

        seeded_l2_cfg: ConfigRecord | None = None
        seeded_l4_cfg: ConfigRecord | None = None

        for spec in DEFAULT_CONFIGS:
            level = int(spec["level"])
            file_id = str(spec["file_id"])
            file_name = str(spec["file_name"])
            display_name = str(spec["display_name"])

            cfg, cfg_created = ConfigRecord.objects.get_or_create(
                level=level,
                file_id=file_id,
                scope=org_scope,
                defaults={
                    "file_name": file_name,
                    "display_name": display_name,
                    "status": ConfigRecord.Status.ACTIVE,
                    "created_by": actor,
                },
            )
            if cfg_created:
                created_records += 1
            else:
                # Keep names/status in sync (non-destructive update)
                dirty = False
                if cfg.file_name != file_name:
                    cfg.file_name = file_name
                    dirty = True
                if cfg.display_name != display_name:
                    cfg.display_name = display_name
                    dirty = True
                if cfg.status != ConfigRecord.Status.ACTIVE:
                    cfg.status = ConfigRecord.Status.ACTIVE
                    dirty = True
                if dirty:
                    cfg.save()

            # Ensure initial version exists
            v, v_created = ConfigVersion.objects.get_or_create(
                config=cfg,
                version="0.0.0",
                defaults={
                    "content_text": DEFAULT_TEXT_BY_LEVEL.get(level, "# (missing default)\n"),
                    "change_note": "Seeded system default (Sandbox slice)",
                    "created_by": actor,
                },
            )
            if v_created:
                created_versions += 1

            if level == 2:
                seeded_l2_cfg = cfg
            elif level == 4:
                seeded_l4_cfg = cfg

        if seeded_l2_cfg is not None:
            pointers.active_l2_config = seeded_l2_cfg
        if seeded_l4_cfg is not None:
            pointers.active_l4_config = seeded_l4_cfg

        pointers.save(update_fields=[
            "active_l2_config",
            "active_l4_config",
            "updated_by",
            "updated_at",
        ])

        self.stdout.write(self.style.SUCCESS("Seed complete (L2 + L4)."))
        self.stdout.write(f"  ConfigRecords created: {created_records}")
        self.stdout.write(f"  ConfigVersions created: {created_versions}")
        self.stdout.write("  SystemConfigPointers updated (id=1): L2 + L4 only")
