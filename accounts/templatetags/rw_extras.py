# -*- coding: utf-8 -*-
# accounts/templatetags/rw_extras.py

from django import template

register = template.Library()

@register.filter
def get_item(d, k):
    try:
        return d.get(k)
    except Exception:
        return None
