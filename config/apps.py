# -*- coding: utf-8 -*-
# config/apps.py

from __future__ import annotations
from django.apps import AppConfig


class ConfigConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "config"
    verbose_name = "Config"
