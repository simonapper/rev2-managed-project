from django.contrib.auth import get_user_model
from django.test import TestCase

from projects.models import Project, WorkItem


class WorkItemPhaseTransitionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="wi_phase_owner",
            email="wi_phase_owner@example.com",
            password="pw",
        )
        self.project = Project.objects.create(name="WorkItem Phase Project", owner=self.owner)
        self.work_item = WorkItem.create_minimal(project=self.project)

    def test_phase_must_be_allowed(self):
        self.assertFalse(self.work_item.can_transition("INVALID"))
        with self.assertRaises(ValueError):
            self.work_item.set_phase("INVALID")

    def test_cannot_transition_to_approve_without_seed_revision(self):
        self.assertFalse(self.work_item.can_transition(WorkItem.PHASE_APPROVE))
        with self.assertRaises(ValueError):
            self.work_item.set_phase(WorkItem.PHASE_APPROVE)

    def test_can_transition_to_approve_with_seed_revision(self):
        self.work_item.append_seed_revision("Seed A", self.owner, "Initial")
        self.assertTrue(self.work_item.can_transition(WorkItem.PHASE_APPROVE))
        out = self.work_item.set_phase(WorkItem.PHASE_APPROVE)
        self.work_item.refresh_from_db()
        self.assertEqual(out, WorkItem.PHASE_APPROVE)
        self.assertEqual(self.work_item.active_phase, WorkItem.PHASE_APPROVE)

    def test_cannot_transition_to_execute_without_pass_locked_seed(self):
        self.assertFalse(self.work_item.can_transition(WorkItem.PHASE_EXECUTE))
        with self.assertRaises(ValueError):
            self.work_item.set_phase(WorkItem.PHASE_EXECUTE)

    def test_can_transition_to_execute_with_pass_locked_seed(self):
        self.work_item.append_seed_revision("Seed A", self.owner, "Initial")
        self.work_item.lock_seed(1)

        self.assertTrue(self.work_item.can_transition(WorkItem.PHASE_EXECUTE))
        out = self.work_item.set_phase(WorkItem.PHASE_EXECUTE)
        self.work_item.refresh_from_db()
        self.assertEqual(out, WorkItem.PHASE_EXECUTE)
        self.assertEqual(self.work_item.active_phase, WorkItem.PHASE_EXECUTE)

    def test_cannot_transition_to_complete_before_execute(self):
        self.work_item.append_seed_revision("Seed A", self.owner, "Initial")
        self.work_item.lock_seed(1)
        self.work_item.set_phase(WorkItem.PHASE_APPROVE)

        self.assertFalse(self.work_item.can_transition(WorkItem.PHASE_COMPLETE))
        with self.assertRaises(ValueError):
            self.work_item.set_phase(WorkItem.PHASE_COMPLETE)

    def test_can_transition_to_complete_after_execute(self):
        self.work_item.append_seed_revision("Seed A", self.owner, "Initial")
        self.work_item.lock_seed(1)
        self.work_item.set_phase(WorkItem.PHASE_EXECUTE)
        self.work_item.add_deliverable("artefact://run-1", note="Execution evidence")

        self.assertTrue(self.work_item.can_transition(WorkItem.PHASE_COMPLETE))
        out = self.work_item.set_phase(WorkItem.PHASE_COMPLETE)
        self.work_item.refresh_from_db()
        self.assertEqual(out, WorkItem.PHASE_COMPLETE)
        self.assertEqual(self.work_item.active_phase, WorkItem.PHASE_COMPLETE)

    def test_cannot_transition_to_complete_without_deliverables(self):
        self.work_item.append_seed_revision("Seed A", self.owner, "Initial")
        self.work_item.lock_seed(1)
        self.work_item.set_phase(WorkItem.PHASE_EXECUTE)

        self.assertFalse(self.work_item.can_transition(WorkItem.PHASE_COMPLETE))
        with self.assertRaises(ValueError):
            self.work_item.set_phase(WorkItem.PHASE_COMPLETE)

    def test_derax_work_requires_locked_endpoint_before_execute(self):
        self.project.workflow_mode = Project.WorkflowMode.DERAX_WORK
        self.project.save(update_fields=["workflow_mode"])

        self.work_item.append_seed_revision("Seed A", self.owner, "Initial")
        self.work_item.lock_seed(1)

        self.assertFalse(self.work_item.can_transition(WorkItem.PHASE_EXECUTE))
        with self.assertRaises(ValueError):
            self.work_item.set_phase(WorkItem.PHASE_EXECUTE)

        self.work_item.set_derax_endpoint("DERAX endpoint spec", actor=self.owner, lock=True)
        self.assertTrue(self.work_item.can_transition(WorkItem.PHASE_EXECUTE))
