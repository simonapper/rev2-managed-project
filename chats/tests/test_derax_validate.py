import json

from django.test import TestCase

from chats.services.derax.schema import empty_payload
from chats.services.derax.validate import derax_json_correction_prompt, validate_derax_response, validate_derax_text


class DeraxValidateTests(TestCase):
    def test_accepts_prose_around_json_object(self):
        payload = empty_payload(phase="DEFINE")
        payload["intent"]["destination"] = "Launch DERAX v1"
        payload["intent"]["constraints"] = ["No LLM integration in this slice"]
        payload["intent"]["non_goals"] = ["No UI drawers yet"]
        text = "Here you go:\n" + json.dumps(payload) + "\n"
        ok, parsed_payload, errors = validate_derax_text(text)
        self.assertTrue(ok, msg=str(errors))
        self.assertIsInstance(parsed_payload, dict)

    def test_accepts_json_only(self):
        payload = empty_payload(phase="DEFINE")
        payload["intent"]["destination"] = "Launch DERAX v1"
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

    def test_accepts_fenced_json_block(self):
        payload = empty_payload(phase="EXPLORE")
        payload["intent"]["destination"] = "Test destination"
        payload["intent"]["success_criteria"] = ["One criterion"]
        payload["explore"]["adjacent_ideas"] = ["One adjacent idea"]
        payload["explore"]["risks"] = ["One risk"]
        payload["explore"]["tradeoffs"] = ["One tradeoff"]
        payload["explore"]["reframes"] = ["One reframe"]
        text = "```json\n" + json.dumps(payload) + "\n```"
        ok, parsed_payload, errors = validate_derax_text(text)
        self.assertTrue(ok, msg=str(errors))
        self.assertIsInstance(parsed_payload, dict)

    def test_accepts_embedded_fenced_json_block(self):
        payload = empty_payload(phase="EXPLORE")
        payload["intent"]["destination"] = "Embedded destination"
        payload["intent"]["success_criteria"] = ["Criterion"]
        payload["explore"]["adjacent_ideas"] = ["Idea"]
        payload["explore"]["risks"] = ["Risk"]
        payload["explore"]["tradeoffs"] = ["Tradeoff"]
        payload["explore"]["reframes"] = ["Reframe"]
        text = "Some preface text.\n```json\n" + json.dumps(payload) + "\n```\nSome trailing text."
        ok, parsed_payload, errors = validate_derax_text(text)
        self.assertTrue(ok, msg=str(errors))
        self.assertIsInstance(parsed_payload, dict)

    def test_accepts_embedded_raw_json_object(self):
        payload = empty_payload(phase="EXPLORE")
        payload["intent"]["destination"] = "Embedded destination"
        payload["intent"]["success_criteria"] = ["Criterion"]
        payload["explore"]["adjacent_ideas"] = ["Idea"]
        payload["explore"]["risks"] = ["Risk"]
        payload["explore"]["tradeoffs"] = ["Tradeoff"]
        payload["explore"]["reframes"] = ["Reframe"]
        text = "Some preface text.\n" + json.dumps(payload) + "\nSome trailing text."
        ok, parsed_payload, errors = validate_derax_text(text)
        self.assertTrue(ok, msg=str(errors))
        self.assertIsInstance(parsed_payload, dict)

    def test_type_mismatch_does_not_crash_normaliser(self):
        payload = {
            "meta": [],
            "canonical_summary": "",
            "intent": [],
            "explore": [],
            "parked_for_later": [],
            "artefacts": [],
            "validation": [],
        }
        ok, parsed_payload, errors = validate_derax_text(json.dumps(payload))
        self.assertFalse(ok)
        self.assertIsInstance(parsed_payload, dict)
        self.assertTrue(len(list(errors or [])) > 0)

    def test_partial_legacy_core_only_shape_is_normalised(self):
        payload = {
            "phase": "EXPLORE",
            "meta": {"phase": "EXPLORE"},
            "core": {
                "end_in_mind": "Core destination",
                "destination_conditions": ["Criterion one"],
                "adjacent_angles": ["Idea one"],
                "risks": ["Risk one"],
                "scope_changes": ["Tradeoff one"],
                "ambiguities": ["Reframe one"],
                "assumptions": [],
                "non_goals": [],
            },
        }
        ok, parsed_payload, errors = validate_derax_text(json.dumps(payload))
        self.assertTrue(ok, msg=str(errors))
        self.assertIsInstance(parsed_payload, dict)

    def test_root_alias_shape_is_normalised(self):
        payload = {
            "phase": "REFINE",
            "destination": "Refine destination",
            "success_criteria": ["Criterion one"],
            "constraints": ["Constraint one"],
            "non_goals": ["Non-goal one"],
            "risks": ["Risk one"],
            "tradeoffs": ["Tradeoff one"],
        }
        ok, parsed_payload, errors = validate_derax_text(json.dumps(payload))
        self.assertTrue(ok, msg=str(errors))
        self.assertIsInstance(parsed_payload, dict)

    def test_wrapped_root_alias_shape_is_normalised(self):
        payload = {
            "response": {
                "phase": "REFINE",
                "destination": "Refine destination",
                "success_criteria": ["Criterion one"],
                "constraints": ["Constraint one"],
                "non_goals": ["Non-goal one"],
                "risks": ["Risk one"],
                "tradeoffs": ["Tradeoff one"],
            }
        }
        ok, parsed_payload, errors = validate_derax_text(json.dumps(payload))
        self.assertTrue(ok, msg=str(errors))
        self.assertIsInstance(parsed_payload, dict)

    def test_legacy_explore_payload_is_normalised_to_canonical(self):
        legacy = {
            "phase": "EXPLORE",
            "headline": "Explore test",
            "core": {
                "end_in_mind": "Reach a stable destination",
                "destination_conditions": ["Condition A"],
                "non_goals": ["No build work"],
                "adjacent_angles": ["Adjacent idea A"],
                "assumptions": ["Assumption A"],
                "ambiguities": ["Reframe A"],
                "risks": ["Risk A"],
                "scope_changes": ["Tradeoff A"],
            },
            "parked": ["Later item"],
            "footnotes": [],
            "next": {"recommended_phase": "EXPLORE", "one_question": ""},
            "meta": {
                "work_item_id": "1",
                "project_id": 1,
                "chat_id": 1,
                "created_at": "2026-02-26T00:00:00Z",
            },
        }
        ok, parsed_payload, errors = validate_derax_text(json.dumps(legacy))
        self.assertTrue(ok, msg=str(errors))
        self.assertIsInstance(parsed_payload, dict)
        self.assertEqual((parsed_payload.get("meta") or {}).get("phase"), "EXPLORE")
        self.assertEqual((parsed_payload.get("intent") or {}).get("destination"), "Reach a stable destination")
        self.assertEqual((parsed_payload.get("explore") or {}).get("adjacent_ideas"), ["Adjacent idea A"])

    def test_refine_alias_fields_fill_from_core(self):
        payload = empty_payload(phase="REFINE")
        payload["meta"]["phase"] = "REFINE"
        payload["core"] = {
            "end_in_mind": "Refined destination",
            "destination_conditions": ["Success A"],
            "non_goals": ["Non-goal A"],
            "adjacent_angles": ["Adjacent A"],
            "assumptions": ["Constraint A"],
            "ambiguities": ["Reframe A"],
            "risks": ["Risk A"],
            "scope_changes": ["Tradeoff A"],
        }
        ok, parsed_payload, errors = validate_derax_text(json.dumps(payload))
        self.assertTrue(ok, msg=str(errors))
        self.assertIsInstance(parsed_payload, dict)
        intent = dict((parsed_payload or {}).get("intent") or {})
        explore = dict((parsed_payload or {}).get("explore") or {})
        self.assertEqual(intent.get("destination"), "Refined destination")
        self.assertEqual(intent.get("success_criteria"), ["Success A"])
        self.assertEqual(intent.get("constraints"), ["Constraint A"])
        self.assertEqual(intent.get("non_goals"), ["Non-goal A"])
        self.assertEqual(explore.get("risks"), ["Risk A"])
        self.assertEqual(explore.get("tradeoffs"), ["Tradeoff A"])

    def test_define_forbids_success_criteria_and_artefacts(self):
        payload = empty_payload(phase="DEFINE")
        payload["intent"]["destination"] = "Destination"
        payload["intent"]["success_criteria"] = ["Not allowed in DEFINE"]
        payload["artefacts"]["proposed"] = [{"kind": "workbook", "title": "Workbook", "notes": ""}]
        ok, _parsed_payload, errors = validate_derax_text(json.dumps(payload))
        self.assertFalse(ok)
        joined = "; ".join(errors)
        self.assertIn("Forbidden content present under intent.success_criteria for phase DEFINE", joined)
        self.assertIn("Forbidden content present under artefacts.proposed for phase DEFINE", joined)

    def test_define_caps_open_questions_trim_required(self):
        payload = empty_payload(phase="DEFINE")
        payload["intent"]["destination"] = "Destination"
        payload["intent"]["open_questions"] = ["Q1", "Q2", "Q3", "Q4"]
        ok, _parsed_payload, errors = validate_derax_text(json.dumps(payload))
        self.assertFalse(ok)
        joined = "; ".join(errors)
        self.assertIn("Cap exceeded: intent.open_questions has 4 items (max 3) for phase DEFINE", joined)

    def test_canonical_summary_word_limit(self):
        payload = empty_payload(phase="DEFINE")
        payload["intent"]["destination"] = "Destination"
        payload["canonical_summary"] = "one two three four five six seven eight nine ten eleven"
        ok, _parsed_payload, errors = validate_derax_text(json.dumps(payload))
        self.assertFalse(ok)
        joined = "; ".join(errors)
        self.assertIn("Cap exceeded: canonical_summary has 11 words (max 10) for phase DEFINE", joined)
