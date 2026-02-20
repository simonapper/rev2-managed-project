# -*- coding: utf-8 -*-

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from chats.models import ChatWorkspace
from chats.services_boundaries import is_boundary_profile_active, resolve_boundary_profile
from chats.services_boundary_validator import validate_boundary_labels
from projects.models import Project


class BoundaryProfileResolveTests(TestCase):
    def test_chat_profile_overlays_project_defaults(self):
        user = get_user_model().objects.create_user(username="b_user", password="pw")
        project = Project.objects.create(
            name="Boundary Overlay Project",
            owner=user,
            boundary_profile_json={
                "jurisdiction": "UK",
                "topic_tags": ["UK_TAX"],
                "authority_set": {"allow_internal_docs": True},
            },
        )
        chat = ChatWorkspace.objects.create(
            project=project,
            created_by=user,
            title="Boundary Chat",
            boundary_profile_json={
                "jurisdiction": "Scotland",
                "authority_set": {"allow_internal_docs": False},
            },
        )

        out = resolve_boundary_profile(project, chat)
        self.assertEqual(out["strictness"], "SOFT")
        self.assertEqual(out["jurisdiction"], "SCOTLAND")
        self.assertEqual(out["topic_tags"], ["UK_TAX"])
        self.assertFalse(out["authority_set"]["allow_internal_docs"])

    def test_default_profile_is_inactive_without_constraints(self):
        out = resolve_boundary_profile(None, None)
        self.assertEqual(out["jurisdiction"], "NONE")
        self.assertFalse(is_boundary_profile_active(out))


class BoundaryValidatorTests(TestCase):
    def test_validate_boundary_labels_pass_and_fail(self):
        profile = {
            "required_labels": {
                "scope_flag": True,
                "assumptions": True,
                "source_basis": True,
                "confidence": True,
            }
        }

        ok_text = "\n".join(
            [
                "Scope: IN-SCOPE",
                "Assumptions: jurisdiction UK.",
                "Source basis: general_knowledge",
                "Confidence: medium",
            ]
        )
        ok, errs = validate_boundary_labels(profile, ok_text)
        self.assertTrue(ok)
        self.assertEqual(errs, [])

        bad_text = "Scope: IN-SCOPE\nConfidence: high"
        ok2, errs2 = validate_boundary_labels(profile, bad_text)
        self.assertFalse(ok2)
        self.assertIn("missing Assumptions", errs2)
        self.assertIn("missing Source basis", errs2)
