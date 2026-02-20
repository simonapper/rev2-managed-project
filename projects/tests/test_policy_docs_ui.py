# -*- coding: utf-8 -*-

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from projects.models import PolicyDocument, Project


class PolicyDocsUiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username="policy_owner", email="po@example.com", password="pw")
        self.project = Project.objects.create(
            name="Policy UI Project",
            owner=self.owner,
            purpose="Test policy docs UI",
            kind=Project.Kind.STANDARD,
        )
        self.client.force_login(self.owner)

    def test_help_page_renders(self):
        resp = self.client.get(reverse("projects:policy_docs_help", args=[self.project.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Policy documents help")

    def test_create_and_delete_policy_doc_from_project_config_info(self):
        url = reverse("accounts:project_config_info", args=[self.project.id])
        create_resp = self.client.post(
            url,
            {
                "action": "policy_doc_create",
                "doc_title": "Handbook",
                "doc_source_ref": "internal://hb/v1",
                "doc_body_text": "Company policy body text.",
            },
        )
        self.assertEqual(create_resp.status_code, 302)
        doc = PolicyDocument.objects.filter(project=self.project, title="Handbook").first()
        self.assertIsNotNone(doc)

        delete_resp = self.client.post(
            url,
            {
                "action": "policy_doc_delete",
                "doc_id": str(doc.id),
            },
        )
        self.assertEqual(delete_resp.status_code, 302)
        self.assertFalse(PolicyDocument.objects.filter(id=doc.id).exists())

