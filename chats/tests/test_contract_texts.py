# -*- coding: utf-8 -*-

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from chats.models import ContractText
from chats.services.contracts.texts import resolve_contract_text
from projects.models import Project


class ContractTextResolverTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="contract_text_u", email="contract_text_u@example.com", password="pw")
        self.other_user = User.objects.create_user(username="contract_text_u2", email="contract_text_u2@example.com", password="pw")
        self.project = Project.objects.create(name="Contract Scope Project", owner=self.user)

    def test_resolver_prefers_user_over_default(self):
        ContractText.objects.create(
            key="tone",
            scope_type=ContractText.ScopeType.GLOBAL_DEFAULT,
            scope_id=None,
            status=ContractText.Status.ACTIVE,
            text="Default tone",
            updated_by=self.user,
        )
        ContractText.objects.create(
            key="tone",
            scope_type=ContractText.ScopeType.USER,
            scope_id=self.user.id,
            status=ContractText.Status.ACTIVE,
            text="User tone",
            updated_by=self.user,
        )

        resolved = resolve_contract_text(self.user, "tone")
        self.assertEqual(resolved["default_text"], "Default tone")
        self.assertEqual(resolved["user_text"], "User tone")
        self.assertEqual(resolved["effective_text"], "User tone")
        self.assertEqual(resolved["effective_source"], "USER")

    def test_resolver_prefers_project_user_when_project_context_present(self):
        ContractText.objects.create(
            key="tone",
            scope_type=ContractText.ScopeType.GLOBAL_DEFAULT,
            status=ContractText.Status.ACTIVE,
            text="Default tone",
            updated_by=self.user,
        )
        ContractText.objects.create(
            key="tone",
            scope_type=ContractText.ScopeType.USER,
            scope_user=self.user,
            status=ContractText.Status.ACTIVE,
            text="Global user tone",
            updated_by=self.user,
        )
        ContractText.objects.create(
            key="tone",
            scope_type=ContractText.ScopeType.PROJECT,
            scope_project=self.project,
            status=ContractText.Status.ACTIVE,
            text="Project tone",
            updated_by=self.user,
        )
        ContractText.objects.create(
            key="tone",
            scope_type=ContractText.ScopeType.PROJECT_USER,
            scope_project=self.project,
            scope_user=self.user,
            status=ContractText.Status.ACTIVE,
            text="Project user tone",
            updated_by=self.user,
        )

        resolved = resolve_contract_text(self.user, "tone", project_id=self.project.id)
        self.assertEqual(resolved["effective_text"], "Project user tone")
        self.assertEqual(resolved["effective_source"], "PROJECT_USER")

    def test_resolver_prefers_project_over_global_user_for_other_user(self):
        ContractText.objects.create(
            key="tone",
            scope_type=ContractText.ScopeType.GLOBAL_DEFAULT,
            status=ContractText.Status.ACTIVE,
            text="Default tone",
            updated_by=self.user,
        )
        ContractText.objects.create(
            key="tone",
            scope_type=ContractText.ScopeType.USER,
            scope_user=self.other_user,
            status=ContractText.Status.ACTIVE,
            text="Other user global tone",
            updated_by=self.user,
        )
        ContractText.objects.create(
            key="tone",
            scope_type=ContractText.ScopeType.PROJECT,
            scope_project=self.project,
            status=ContractText.Status.ACTIVE,
            text="Project tone",
            updated_by=self.user,
        )

        resolved = resolve_contract_text(self.other_user, "tone", project_id=self.project.id)
        self.assertEqual(resolved["effective_text"], "Project tone")
        self.assertEqual(resolved["effective_source"], "PROJECT")

    def test_invalid_define_override_falls_back_to_default(self):
        ContractText.objects.create(
            key="phase.define",
            scope_type=ContractText.ScopeType.GLOBAL_DEFAULT,
            status=ContractText.Status.ACTIVE,
            text="Safe default. Return JSON only. Do NOT generate success criteria. No markdown.",
            updated_by=self.user,
        )
        ContractText.objects.create(
            key="phase.define",
            scope_type=ContractText.ScopeType.PROJECT_USER,
            scope_project=self.project,
            scope_user=self.user,
            status=ContractText.Status.ACTIVE,
            text="Generate success criteria and provide markdown.",
            updated_by=self.user,
        )

        resolved = resolve_contract_text(self.user, "phase.define", project_id=self.project.id)
        self.assertEqual(resolved["effective_source"], "DEFAULT")
        self.assertIn("Do NOT generate success criteria", str(resolved["effective_text"]))

    def test_invalid_define_default_is_not_effective(self):
        ContractText.objects.create(
            key="phase.define",
            scope_type=ContractText.ScopeType.GLOBAL_DEFAULT,
            status=ContractText.Status.ACTIVE,
            text="Generate success criteria and provide markdown.",
            updated_by=self.user,
        )

        resolved = resolve_contract_text(self.user, "phase.define", project_id=self.project.id)
        self.assertEqual(resolved["effective_source"], "DEFAULT_INVALID")
        self.assertEqual(str(resolved["effective_text"] or ""), "")
