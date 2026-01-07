# objects/models.py
# -*- coding: utf-8 -*-

from django.conf import settings
from django.db import models


class KnowledgeObject(models.Model):
    """
    Base table for all durable knowledge objects.

    Object-type-specific rules are enforced at the governance layer,
    not via subclassing.
    """
    class ObjectType(models.TextChoices):
        CKO = "CKO", "Canonical Knowledge Object"
        WKO = "WKO", "Workflow Knowledge Object"
        DKO = "DKO", "Derived Knowledge Object"
        TKO = "TKO", "Transitional Knowledge Object"
        PKO = "PKO", "Personal Knowledge Object"

    class Classification(models.TextChoices):
        PUBLIC = "PUBLIC", "Public"
        INTERNAL = "INTERNAL", "Internal"
        CONFIDENTIAL = "CONFIDENTIAL", "Confidential"
        RESTRICTED = "RESTRICTED", "Restricted"

    class Status(models.TextChoices):
        CANDIDATE = "CANDIDATE", "Candidate"
        ACCEPTED = "ACCEPTED", "Accepted"
        CONTESTED = "CONTESTED", "Contested"
        REJECTED_REWORK = "REJECTED_REWORK", "Rejected: Rework"
        REJECTED_CLOSED = "REJECTED_CLOSED", "Rejected: Closed"
        ACTIVE = "ACTIVE", "Active"
        SUPERSEDED = "SUPERSEDED", "Superseded"
        CLOSED = "CLOSED", "Closed"

    object_type = models.CharField(max_length=10, choices=ObjectType.choices)

    # Draft or local identifier (pre-official)
    local_id = models.CharField(max_length=120, blank=True)

    # System-issued canonical ID (only for ACCEPTED objects)
    official_id = models.CharField(max_length=120, blank=True)

    title = models.CharField(max_length=300)
    canonical_summary = models.CharField(max_length=200, blank=True)

    domain = models.CharField(max_length=200, blank=True)
    scope_text = models.TextField(blank=True)

    status = models.CharField(max_length=30, choices=Status.choices, default=Status.CANDIDATE)
    classification = models.CharField(max_length=20, choices=Classification.choices, default=Classification.INTERNAL)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="knowledge_objects",
    )

    # Global objects (e.g. governance) have project = NULL
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="knowledge_objects",
    )


    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class KnowledgeObjectVersion(models.Model):
    """
    Immutable version of a knowledge object's content.
    """
    obj = models.ForeignKey(KnowledgeObject, on_delete=models.CASCADE, related_name="versions")
    version = models.CharField(max_length=30)
    content_text = models.TextField()
    change_note = models.CharField(max_length=500, blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class KnowledgeLink(models.Model):
    """
    Explicit relationship between two knowledge objects.
    """
    class LinkType(models.TextChoices):
        REFERENCES = "REFERENCES", "References"
        DERIVED_FROM = "DERIVED_FROM", "Derived from"
        SUPERSEDES = "SUPERSEDES", "Supersedes"
        SUPERSEDED_BY = "SUPERSEDED_BY", "Superseded by"
        DISPUTES = "DISPUTES", "Disputes"
        IMPLEMENTS = "IMPLEMENTS", "Implements"

    from_object = models.ForeignKey(KnowledgeObject, on_delete=models.CASCADE, related_name="outgoing_links")
    to_object = models.ForeignKey(KnowledgeObject, on_delete=models.CASCADE, related_name="incoming_links")
    link_type = models.CharField(max_length=30, choices=LinkType.choices)
    note = models.CharField(max_length=500, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
