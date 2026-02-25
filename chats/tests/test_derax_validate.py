import json

from django.test import TestCase

from chats.services.derax.schema import empty_payload
from chats.services.derax.validate import derax_json_correction_prompt, validate_derax_response, validate_derax_text


class DeraxValidateTests(TestCase):
    def test_rejects_prose_around_json(self):
        payload = empty_payload(phase="DEFINE")
        payload["intent"]["destination"] = "Launch DERAX v1"
        payload["intent"]["success_criteria"] = ["Schema and rules implemented"]
        payload["intent"]["constraints"] = ["No LLM integration in this slice"]
        payload["intent"]["non_goals"] = ["No UI drawers yet"]
        text = "Here you go:\n" + json.dumps(payload) + "\n"
        ok, parsed_payload, errors = validate_derax_text(text)
        self.assertFalse(ok)
        self.assertIsNone(parsed_payload)
        self.assertIn("Non-JSON content outside JSON object", errors)

    def test_accepts_json_only(self):
        payload = empty_payload(phase="DEFINE")
        payload["intent"]["destination"] = "Launch DERAX v1"
        payload["intent"]["success_criteria"] = ["Schema and rules implemented"]
        payload["intent"]["constraints"] = ["No LLM integration in this slice"]
        payload["intent"]["non_goals"] = ["No UI drawers yet"]
        text = json.dumps(payload)
        ok, parsed_payload, errors = validate_derax_text(text)
        self.assertTrue(ok)
        self.assertIsInstance(parsed_payload, dict)
        self.assertEqual(errors, [])

    def test_invalid_json_fails_parse(self):
        ok, parsed_payload, errors = validate_derax_text("{ not json }")
        self.assertFalse(ok)
        self.assertIsNone(parsed_payload)
        self.assertTrue(any("Invalid JSON:" in err for err in errors))

    def test_missing_required_fields_fails_phase_checks(self):
        text = json.dumps(empty_payload(phase="DEFINE"))
        ok, parsed_payload, errors = validate_derax_text(text)
        self.assertFalse(ok)
        self.assertIsInstance(parsed_payload, dict)
        self.assertIn("Missing or empty: intent.destination", errors)
        self.assertIn("Missing or empty: intent.success_criteria", errors)

    def test_wrapper_returns_canonical_error_not_legacy_error(self):
        text = json.dumps(empty_payload(phase="DEFINE"))
        ok, payload_or_error = validate_derax_response(text)
        self.assertFalse(ok)
        self.assertIsInstance(payload_or_error, str)
        self.assertIn("Missing or empty: intent.destination", payload_or_error)
        self.assertNotIn("legacy DERAX schema", payload_or_error)

    def test_correction_prompt_includes_phase_and_template(self):
        prompt = derax_json_correction_prompt("Missing or empty: intent.destination", phase="DEFINE")
        self.assertIn("Set meta.phase to: DEFINE", prompt)
        self.assertIn('"meta"', prompt)
        self.assertIn('"intent"', prompt)
