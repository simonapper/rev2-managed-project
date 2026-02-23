from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from projects.models import Project, WorkItem


class WorkItemUiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="workitem_ui_owner",
            email="workitem_ui_owner@example.com",
            password="pw",
        )
        self.project = Project.objects.create(name="WorkItem UI Project", owner=self.owner)
        self.client.force_login(self.owner)

    def test_detail_view_renders_and_shows_core_fields(self):
        url = reverse("projects:work_item_detail", args=[self.project.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Active phase:")
        self.assertContains(resp, "Active seed revision:")
        self.assertContains(resp, "Deliverables")

    def test_propose_lock_rollback_actions(self):
        url = reverse("projects:work_item_detail", args=[self.project.id])
        self.client.post(
            url,
            {
                "action": "propose_seed",
                "seed_text": "Seed one",
                "reason": "Initial",
            },
        )
        wi = WorkItem.objects.filter(project=self.project).order_by("-id").first()
        self.assertIsNotNone(wi)
        self.assertEqual(wi.active_seed_revision, 1)
        self.assertEqual(wi.seed_log[0]["status"], WorkItem.SEED_STATUS_PROPOSED)

        self.client.post(url, {"action": "lock_seed", "revision_number": "1"})
        wi.refresh_from_db()
        self.assertEqual(wi.seed_log[0]["status"], WorkItem.SEED_STATUS_PASS_LOCKED)

        self.client.post(url, {"action": "rollback_seed", "revision_number": "1"})
        wi.refresh_from_db()
        self.assertEqual(wi.active_seed_revision, 2)
        self.assertEqual(wi.seed_log[-1]["event"], "ROLLBACK")

    def test_advance_phase_only_when_allowed(self):
        url = reverse("projects:work_item_detail", args=[self.project.id])
        self.client.post(url, {"action": "advance_phase", "to_phase": WorkItem.PHASE_EXECUTE})
        wi = WorkItem.objects.filter(project=self.project).order_by("-id").first()
        self.assertEqual(wi.active_phase, WorkItem.PHASE_DEFINE)

        self.client.post(
            url,
            {
                "action": "propose_seed",
                "seed_text": "Seed one",
                "reason": "Initial",
            },
        )
        self.client.post(url, {"action": "lock_seed", "revision_number": "1"})
        self.client.post(url, {"action": "advance_phase", "to_phase": WorkItem.PHASE_EXECUTE})
        wi.refresh_from_db()
        self.assertEqual(wi.active_phase, WorkItem.PHASE_EXECUTE)

    def test_derax_endpoint_actions_and_execute_gate(self):
        self.project.workflow_mode = Project.WorkflowMode.DERAX_WORK
        self.project.save(update_fields=["workflow_mode"])
        url = reverse("projects:work_item_detail", args=[self.project.id])

        self.client.post(
            url,
            {
                "action": "propose_seed",
                "seed_text": "Seed one",
                "reason": "Initial",
            },
        )
        self.client.post(url, {"action": "lock_seed", "revision_number": "1"})
        self.client.post(url, {"action": "advance_phase", "to_phase": WorkItem.PHASE_EXECUTE})
        wi = WorkItem.objects.filter(project=self.project).order_by("-id").first()
        self.assertEqual(wi.active_phase, WorkItem.PHASE_DEFINE)

        self.client.post(
            url,
            {
                "action": "save_derax_endpoint",
                "derax_endpoint_spec": "DERAX endpoint draft spec",
            },
        )
        wi.refresh_from_db()
        self.assertFalse(wi.derax_endpoint_locked)
        self.assertEqual(wi.derax_endpoint_spec, "DERAX endpoint draft spec")

        self.client.post(url, {"action": "lock_derax_endpoint"})
        wi.refresh_from_db()
        self.assertTrue(wi.derax_endpoint_locked)

        self.client.post(url, {"action": "advance_phase", "to_phase": WorkItem.PHASE_EXECUTE})
        wi.refresh_from_db()
        self.assertEqual(wi.active_phase, WorkItem.PHASE_EXECUTE)
