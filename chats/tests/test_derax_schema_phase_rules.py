from django.test import TestCase

from chats.services.derax.phase_rules import check_required_nonempty, required_paths_for_phase
from chats.services.derax.schema import empty_payload, validate_structural


class DeraxSchemaPhaseRulesTests(TestCase):
    def test_empty_payload_has_all_keys_and_validates_structural(self):
        payload = empty_payload("DEFINE")
        self.assertIn("meta", payload)
        self.assertIn("canonical_summary", payload)
        self.assertIn("intent", payload)
        self.assertIn("explore", payload)
        self.assertIn("parked_for_later", payload)
        self.assertIn("artefacts", payload)
        self.assertIn("validation", payload)
        ok, errs = validate_structural(payload)
        self.assertTrue(ok, msg=str(errs))

    def test_define_empty_payload_fails_required_nonempty(self):
        payload = empty_payload("DEFINE")
        payload["meta"]["phase"] = "DEFINE"
        ok, errs = check_required_nonempty(payload)
        self.assertFalse(ok)
        self.assertTrue(any("intent.destination" in e for e in errs))
        self.assertTrue(any("intent.success_criteria" in e for e in errs))

    def test_define_minimal_payload_passes_required_nonempty(self):
        payload = empty_payload("DEFINE")
        payload["meta"]["phase"] = "DEFINE"
        payload["intent"]["destination"] = "Clear end state"
        payload["intent"]["success_criteria"] = ["Outcome is clear"]
        payload["intent"]["constraints"] = ["Constraint A"]
        payload["intent"]["non_goals"] = ["No execution plan"]
        ok, errs = check_required_nonempty(payload)
        self.assertTrue(ok, msg=str(errs))

    def test_required_paths_for_phase_returns_define_paths(self):
        paths = required_paths_for_phase("DEFINE")
        self.assertIn("intent.destination", paths)
        self.assertIn("intent.success_criteria", paths)
