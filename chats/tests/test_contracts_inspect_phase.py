# -*- coding: utf-8 -*-

from __future__ import annotations

from django.test import SimpleTestCase

from chats.services.contracts.inspect import get_raw_contract_text
from chats.services.contracts.pipeline import ContractContext


class ContractInspectPhaseTests(SimpleTestCase):
    def test_phase_define_returns_static_contract_text(self):
        ctx = ContractContext()
        text = str(get_raw_contract_text(ctx, "phase.define") or "").strip()
        self.assertTrue(text)
        self.assertIn("Source: Phase DEFINE", text)
        self.assertNotIn("WorkItem title:", text)

