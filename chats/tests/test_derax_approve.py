from django.test import SimpleTestCase

from chats.services.derax.approve import apply_approval_results_to_payload, evaluate_approval
from chats.services.derax.schema import empty_payload


class DeraxApproveTests(SimpleTestCase):
    def test_evaluate_approval_basic_warnings(self):
        payload = empty_payload("REFINE")
        payload["meta"]["phase"] = "2 HOUR SESSION"
        payload["intent"]["destination"] = "Align on a stable destination."
        payload["intent"]["success_criteria"] = ["Shared outcome statement."]
        payload["intent"]["constraints"] = ["2-hour session only."]
        payload["intent"]["non_goals"] = ["No route planning."]
        payload["explore"]["risks"] = ["Over-specification risk."]
        payload["explore"]["tradeoffs"] = ["Depth vs pace."]
        payload["intent"]["assumptions"] = ["Detailed owners and thresholds can wait."]
        payload["intent"]["open_questions"] = ["Which priority first?"]
        payload["canonical_summary"] = ""

        results = evaluate_approval(payload)

        self.assertEqual(results["schema_ok"], "yes")
        self.assertIn("Missing canonical_summary", results["warnings"])
        self.assertIn("Mechanism detail present for 2-hour session - compress to principle level", results["warnings"])
        self.assertEqual(results["errors"], [])

    def test_evaluate_approval_blocking_error_on_missing_destination(self):
        payload = empty_payload("REFINE")
        payload["intent"]["destination"] = ""
        payload["intent"]["success_criteria"] = ["One"]
        payload["intent"]["constraints"] = ["No route plan"]
        payload["intent"]["non_goals"] = ["No tactics"]
        payload["explore"]["risks"] = ["Risk"]
        payload["explore"]["tradeoffs"] = ["Tradeoff"]

        results = evaluate_approval(payload)

        self.assertEqual(results["schema_ok"], "no")
        self.assertIn("intent.destination is missing", results["errors"])

    def test_apply_approval_results_to_payload(self):
        payload = empty_payload("APPROVE")
        results = {
            "schema_ok": "yes",
            "warnings": ["warning 1"],
            "errors": [],
            "suggested_action": ["action 1"],
        }
        updated = apply_approval_results_to_payload(payload, results)
        self.assertEqual(updated["validation"]["schema_ok"], "yes")
        self.assertEqual(updated["validation"]["errors"], [])
        self.assertEqual(updated["validation"]["warnings"], ["warning 1"])

