from django.test import TestCase

from chats.services.derax.phase_rules import check_required_nonempty
from chats.services.derax.schema import empty_payload


class DeraxPhaseRulesTests(TestCase):
    def test_empty_define_fails_required_checks(self):
        payload = empty_payload(phase="DEFINE")
        ok, errors = check_required_nonempty(payload)
        self.assertFalse(ok)
        self.assertIn("Missing or empty: intent.destination", errors)
        self.assertIn("Missing or empty: intent.success_criteria", errors)

    def test_minimal_define_passes_required_checks(self):
        payload = empty_payload(phase="DEFINE")
        payload["intent"]["destination"] = "Launch DERAX v1"
        payload["intent"]["success_criteria"] = ["Schema and rules implemented"]
        ok, errors = check_required_nonempty(payload)
        self.assertTrue(ok)
        self.assertEqual(errors, [])

    def test_missing_phase_fails_with_clear_error(self):
        payload = empty_payload(phase="")
        ok, errors = check_required_nonempty(payload)
        self.assertFalse(ok)
        self.assertEqual(errors, ["Missing or empty: meta.phase"])
