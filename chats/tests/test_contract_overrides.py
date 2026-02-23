# -*- coding: utf-8 -*-

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from chats.models import ContractOverride
from chats.services.contracts.pipeline import ContractContext, build_system_blocks


class ContractOverrideTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="override_u", email="override_u@example.com", password="pw")

    def test_override_applied_uses_override_text(self):
        ContractOverride.objects.create(
            key="envelope.json_schema",
            scope_type=ContractOverride.ScopeType.GLOBAL,
            is_enabled=True,
            override_text="OVERRIDE ENVELOPE TEXT",
            updated_by=self.user,
        )
        blocks, trace = build_system_blocks(ContractContext(user=self.user))
        self.assertTrue(blocks)
        self.assertEqual(blocks[0], "OVERRIDE ENVELOPE TEXT")
        self.assertTrue(trace["ordered_blocks"][0].get("override_applied"))

    def test_override_disabled_uses_raw_text(self):
        ContractOverride.objects.create(
            key="envelope.json_schema",
            scope_type=ContractOverride.ScopeType.GLOBAL,
            is_enabled=False,
            override_text="OVERRIDE ENVELOPE TEXT",
            updated_by=self.user,
        )
        blocks, trace = build_system_blocks(ContractContext(user=self.user))
        self.assertTrue(blocks)
        self.assertIn("Return JSON with keys:", blocks[0])
        self.assertFalse(trace["ordered_blocks"][0].get("override_applied"))
