# -*- coding: utf-8 -*-
# projects/context_processors.py
#
# Purpose:
# Provide UI mode flags so base.html can swap topbar/sidebar for PDE.

from __future__ import annotations


def ui_mode(request):
    path = (getattr(request, "path", "") or "")
    rm = getattr(request, "resolver_match", None)
    view_name = getattr(rm, "view_name", "") if rm else ""

    # Treat any PPDE route as planning mode.
    is_ppde = False
    if "/ppde/" in path:
        is_ppde = True
    if view_name.startswith("projects:ppde_"):
        is_ppde = True

    # Treat any PDE route as "definition mode" (exclude PPDE paths).
    is_pde = False
    if "/pde/" in path and "/ppde/" not in path:
        is_pde = True
    if view_name.startswith("projects:pde_"):
        is_pde = True
    if is_ppde:
        is_pde = False

    # Simple return target. Adjust if you prefer project detail.
    return_to = "/accounts/dashboard/"

    return {
        "ui_is_pde": is_pde,
        "ui_is_ppde": is_ppde,
        "ui_hide_sidebar": is_ppde,
        "ui_return_to": return_to,
        "ui_view_name": view_name,
    }
