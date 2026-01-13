# -*- coding: utf-8 -*-
# accounts/templatetags/rw_extras.py

from __future__ import annotations

from django import template

register = template.Library()


@register.filter
def get_item(d, key):
    if d is None:
        return None
    try:
        return d.get(key)
    except AttributeError:
        try:
            return d[key]
        except Exception:
            return None
