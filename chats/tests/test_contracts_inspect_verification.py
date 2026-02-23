# -*- coding: utf-8 -*-

from __future__ import annotations

from django.test import TestCase

from chats.services.contracts.inspect import get_raw_contract_text
from chats.services.contracts.pipeline import ContractContext


class ContractInspectVerificationTests(TestCase):
    def test_verification_keys_return_non_empty_text(self):
        ctx = ContractContext()
        keys = [
            "pde.validator.boilerplate",
            "pde.draft.boilerplate",
            "cde.validator.boilerplate",
            "cde.draft.boilerplate",
            "cko.review.system_block",
        ]
        for key in keys:
            text = str(get_raw_contract_text(ctx, key) or "").strip()
            self.assertTrue(text, msg=f"Expected non-empty raw text for {key}")

