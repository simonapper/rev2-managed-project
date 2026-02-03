# -*- coding: utf-8 -*-
# projects/services/pde_required_keys.py
#
# PDE v1 - Convenience list for commit gate.
# NOTE: Keep code comments 7-bit ASCII only.

from __future__ import annotations

from projects.services.pde_spec import PDE_REQUIRED_FIELDS


def pde_required_keys_for_defined() -> list[str]:
    # All required specs must be PASS_LOCKED to commit.
    return [s.key for s in PDE_REQUIRED_FIELDS if bool(s.required)]
