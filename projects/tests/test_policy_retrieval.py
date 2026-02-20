# -*- coding: utf-8 -*-

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from projects.models import PolicyDocument, Project
from projects.services_policy_retrieval import policy_retrieve


class PolicyRetrievalTests(TestCase):
    def test_policy_retrieve_returns_excerpt_on_keyword_match(self):
        user = get_user_model().objects.create_user(username="policy_u", password="pw")
        project = Project.objects.create(name="Policy Retrieval Project", owner=user)
        PolicyDocument.objects.create(
            project=project,
            title="UK Tax Internal Note",
            body_text="VAT thresholds changed. Review deadlines and HMRC guidance each quarter.",
            source_ref="internal://tax-note-1",
        )

        out = policy_retrieve(project, "Need tax threshold guidance", max_chars=120)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "UK Tax Internal Note")
        self.assertTrue(out[0]["excerpt"])
