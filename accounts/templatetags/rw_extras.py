# -*- coding: utf-8 -*-
# accounts/templatetags/rw_extras.py

from __future__ import annotations

import re

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


def _to_lines(value) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"^\s*[-*]\s+", "", raw.strip())
        line = re.sub(r"^\s*\d+[.)]\s+", "", line)
        line = line.strip()
        if line:
            lines.append(line)
    return lines


@register.filter(name="list_items")
def list_items(value):
    return _to_lines(value)


@register.filter(name="first_line")
def first_line(value):
    lines = _to_lines(value)
    return lines[0] if lines else ""


@register.filter(name="count_items")
def count_items(value):
    return len(_to_lines(value))


@register.filter(name="preview")
def preview(value, n=120):
    text = str(value or "").strip()
    try:
        limit = int(n)
    except Exception:
        limit = 120
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."
