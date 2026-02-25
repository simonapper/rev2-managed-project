from django.contrib.auth import get_user_model
from django.test import TestCase

from projects.models import Project, WorkItem
from projects.services_phase_output_validator import validate_phase_output


class PhaseOutputValidatorTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="phase_validator_owner",
            email="phase_validator_owner@example.com",
            password="pw",
        )
        self.project = Project.objects.create(name="Phase Validator Project", owner=self.owner)
        self.work_item = WorkItem.create_minimal(project=self.project, active_phase=WorkItem.PHASE_REFINE)

    def test_missing_header_triggers_failure(self):
        text = "core.end_in_mind\nSeed content only."
        ok, missing = validate_phase_output(work_item=self.work_item, text=text)
        self.assertFalse(ok)
        self.assertIn("core.destination_conditions", missing)
        self.assertIn("core.assumptions", missing)

    def test_all_headers_present_passes(self):
        text = (
            "core.end_in_mind\n"
            "Summary.\n\n"
            "core.destination_conditions\n"
            "Inputs.\n\n"
            "core.assumptions\n"
            "Outputs.\n\n"
            "core.ambiguities\n"
            "Ambiguities.\n\n"
            "next.one_question\n"
            "Question.\n"
        )
        ok, missing = validate_phase_output(work_item=self.work_item, text=text)
        self.assertTrue(ok)
        self.assertEqual(missing, [])

    def test_boundary_present_missing_label_fails_validation(self):
        self.work_item.boundary_profile_json = {
            "jurisdiction": "UK",
            "topic_tags": ["UK_LAW"],
            "authority_set": {
                "allow_model_general_knowledge": True,
                "allow_internal_docs": True,
                "allow_public_sources": False,
            },
            "strictness": "SOFT",
            "out_of_scope_behaviour": "ALLOW_WITH_WARNING",
            "required_labels": ["Scope", "Assumptions", "Source basis", "Confidence"],
        }
        self.work_item.save(update_fields=["boundary_profile_json", "updated_at"])

        text = (
            "core.end_in_mind\n"
            "Summary.\n\n"
            "core.destination_conditions\n"
            "Inputs.\n\n"
            "core.assumptions\n"
            "Outputs.\n\n"
            "core.ambiguities\n"
            "Ambiguities.\n\n"
            "next.one_question\n"
            "Question.\n\n"
            "Scope: IN-SCOPE\n"
            "Assumptions: baseline assumptions.\n"
            "Confidence: medium\n"
        )
        ok, missing = validate_phase_output(work_item=self.work_item, text=text)
        self.assertFalse(ok)
        self.assertIn("Source basis:", missing)

    def test_boundary_absent_label_enforcement_off(self):
        self.work_item.boundary_profile_json = {}
        self.work_item.save(update_fields=["boundary_profile_json", "updated_at"])

        text = (
            "core.end_in_mind\n"
            "Summary.\n\n"
            "core.destination_conditions\n"
            "Inputs.\n\n"
            "core.assumptions\n"
            "Outputs.\n\n"
            "core.ambiguities\n"
            "Ambiguities.\n\n"
            "next.one_question\n"
            "Question.\n"
        )
        ok, missing = validate_phase_output(work_item=self.work_item, text=text)
        self.assertTrue(ok)
        self.assertEqual(missing, [])
