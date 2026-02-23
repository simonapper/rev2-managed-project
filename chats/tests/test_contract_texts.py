# -*- coding: utf-8 -*-

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from chats.models import ContractText
from chats.services.contracts.texts import resolve_contract_text


class ContractTextResolverTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="contract_text_u", email="contract_text_u@example.com", password="pw")

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
