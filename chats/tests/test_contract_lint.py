# -*- coding: utf-8 -*-

from __future__ import annotations

from django.test import SimpleTestCase

from chats.services.contracts.lint import lint_contract_text


class ContractLintTests(SimpleTestCase):
    def test_define_contract_blocks_success_criteria_instruction(self):
        result = lint_contract_text(
            key="phase.define",
            text="Generate success criteria and return markdown.",
        )
        self.assertFalse(result.get("ok"))
        messages = [str(x.get("message") or "") for x in list(result.get("findings") or [])]
        self.assertTrue(any("success criteria" in m.lower() for m in messages))

    def test_define_contract_passes_minimal_safe_form(self):
        result = lint_contract_text(
            key="phase.define",
            text=(
                "Return JSON only. No markdown.\n"
                "Do NOT generate success criteria.\n"
                "Ask 1-3 high-leverage clarification questions.\n"
                "Include a subtext probe.\n"
                "Use HYPOTHESIS: prefix for assumptions."
            ),
        )
        self.assertTrue(result.get("ok"))
