# -*- coding: utf-8 -*-
# accounts/l1_renderer.py

from __future__ import annotations

from dataclasses import dataclass

from accounts.models_avatars import Avatar, UserProfile


@dataclass(frozen=True)
class L1AvatarSelection:
    cognitive: Avatar
    interaction: Avatar
    presentation: Avatar
    epistemic: Avatar
    performance: Avatar
    checkpointing: Avatar


def _get_profile(user) -> UserProfile:
    # Assumes signals created it. If missing, fail loudly in dev.
    return user.profile  # type: ignore[attr-defined]


def _get_selection(profile: UserProfile) -> L1AvatarSelection:
    return L1AvatarSelection(
        cognitive=profile.cognitive_avatar,
        interaction=profile.interaction_avatar,
        presentation=profile.presentation_avatar,
        epistemic=profile.epistemic_avatar,
        performance=profile.performance_avatar,
        checkpointing=profile.checkpointing_avatar,
    )


def render_level1(user, view: str = "short") -> str:
    """
    Render Level 1 config for a user.

    view:
      - "short": user-facing resolved summary
      - "full" : canonical full text block (includes comments / definitions)

    Note:
    This renderer currently treats avatar implications as canonical defaults.
    In Option 4, avatar definitions can carry their own config blocks.
    """
    if view not in ("short", "full"):
        raise ValueError("view must be 'short' or 'full'")

    profile = _get_profile(user)
    sel = _get_selection(profile)

    if view == "short":
        return _render_short(profile, sel)

    return _render_full(profile, sel)


def _render_short(profile: UserProfile, sel: L1AvatarSelection) -> str:
    return f"""# ============================================================
# LEVEL 1 - USER SETTINGS (EFFECTIVE)
# View: Short (user-facing)
# ============================================================

Language
- Default language: {profile.default_language}
- Default language variant: {profile.get_default_language_variant_display()}
- Language switching permitted: {"ON" if profile.language_switching_permitted else "OFF"}
- Persist language switch for session: {"ON" if profile.persist_language_switch_for_session else "OFF"}

Avatars (one per section)
- Cognitive: {sel.cognitive.name}
- Interaction: {sel.interaction.name}
- Presentation: {sel.presentation.name}
- Epistemic: {sel.epistemic.name}
- Performance: {sel.performance.name}
- Checkpointing: {sel.checkpointing.name}

Effective Defaults (high-level)
- Reasoning shown by default: OFF
- Reasoning available on request: REQUIRED
- Response mode: Answer-first
- Scope drift sensitivity: HIGH
- Checkpointing: Prompting enabled; no automatic export
# ============================================================
""".rstrip() + "\n"


def _render_full(profile: UserProfile, sel: L1AvatarSelection) -> str:
    # This is intentionally your canonical structure but with the
    # defaults filled from the user's avatar choices + language.
    #
    # For now, we include the long explanatory comments as fixed text.
    # In Option 4, you can move these into DB-backed avatar blocks.

    return f"""# ============================================================
# FILE NAME: Level 1 user_settings.conf
# LEVEL 1 - USER SETTINGS  (CONFIG)
# Purpose: Define personal cognitive, interaction, epistemic,
#          performance, and checkpointing defaults.
#
# SCOPE:
# Global for this user.
# ============================================================
# ============================================================

Language Settings

- Default language: {profile.default_language}
- Default language variant: {profile.get_default_language_variant_display()}

- Language switching permitted: {"ON" if profile.language_switching_permitted else "OFF"}
# User may request a different language at any time.

- Persist language switch for session when explicitly requested: {"ON" if profile.persist_language_switch_for_session else "OFF"}
# Example: "From now on, respond in Spanish."

Cognitive Profile

# Cognitive Avatars
# - Analyst:
#   Structured, stage-disciplined, logic-first, fidelity-first.
#   Required for CKO creation and approval.
#
# - Artist:
#   Fluid, expressive, coherence-first, generative.
#   Suitable for creative and exploratory work only.
#
# - Advocate:
#   Structured but rhetorical, persuasion-oriented.
#   Suitable for teaching, leadership, and communication.
#
# - Explorer:
#   Open-ended, logic-honest, tension-preserving.
#   Suitable for early research and hypothesis formation.
#
# Avatar selection must be explicit.
# Epistemic commitments and authority models always apply.

Default Cognitive Avatar: {sel.cognitive.name}

Interaction Preferences

# Interaction Avatars define how responses are presented and
# how the conversation behaves. They affect display, tone,
# brevity, and pushback style, but do NOT affect reasoning,
# authority models, or governance rules.

Default Interaction Avatar: {sel.interaction.name}

Presentation & Explanation Preferences

Default Presentation Avatar: {sel.presentation.name}

Epistemic Commitments

# Epistemic Avatars define how truth claims are handled.

Default Epistemic Avatar: {sel.epistemic.name}

Performance Preferences

Default Performance Avatar: {sel.performance.name}

Checkpointing Preferences

Default Checkpointing Avatar: {sel.checkpointing.name}

Override Policy
- User may explicitly override any setting: ON
- No implicit overrides assumed: ON

# ============================================================
# End of Level 1 - User Settings (Config)
# ============================================================
""".rstrip() + "\n"
