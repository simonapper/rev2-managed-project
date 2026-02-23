from django.contrib.auth import get_user_model
from django.test import TestCase

from projects.models import Project, WorkItem
from projects.services_workitem_finalise import finalise_work_item


class WorkItemFinaliseTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="workitem_finalise_owner",
            email="workitem_finalise_owner@example.com",
            password="pw",
        )
        self.project = Project.objects.create(name="WorkItem Finalise Project", owner=self.owner)

    def test_cannot_finalise_without_deliverables(self):
        work_item = WorkItem.create_minimal(project=self.project, active_phase=WorkItem.PHASE_EXECUTE)
        work_item.append_seed_revision("Locked seed", self.owner, "Initial")
        work_item.lock_seed(1)

        with self.assertRaises(ValueError):
            finalise_work_item(work_item)

    def test_final_summary_includes_artefact_index_and_rollback_point(self):
        work_item = WorkItem.create_minimal(project=self.project, active_phase=WorkItem.PHASE_EXECUTE)
        work_item.append_seed_revision("Locked seed", self.owner, "Initial")
        work_item.lock_seed(1)
        work_item.add_deliverable("artefact://codex_instruction", note="Instruction document")

        summary = finalise_work_item(work_item)
        work_item.refresh_from_db()

        self.assertIn("# Artefact index (list of deliverables)", summary)
        self.assertIn("artefact://codex_instruction", summary)
        self.assertIn("# Rollback point (active seed revision number)", summary)
        self.assertIn("\n1\n", summary)
        self.assertEqual(work_item.state, "COMPLETE")
        self.assertEqual(work_item.active_phase, WorkItem.PHASE_COMPLETE)

