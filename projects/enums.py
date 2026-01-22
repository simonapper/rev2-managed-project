# -*- coding: utf-8 -*-
# projects/enums.py
# Purpose: Permissions on chats within a project

from django.db import models

class ChatReadScope(models.TextChoices):
    OWNER_ONLY = "OWNER_ONLY", "Owner only"
    PROJECT_MANAGERS = "PROJECT_MANAGERS", "Project owner + project managers"
    ANY_MANAGER = "ANY_MANAGER", "Org managers (global)"
