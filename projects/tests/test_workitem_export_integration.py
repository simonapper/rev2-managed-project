import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from projects.models import Project, WorkItem
from projects.services_codex_instruction import generate_codex_instruction
from projects.services_workitem_finalise import finalise_work_item


class WorkItemExportIntegrationTests(TestCase):
    def test_export_contains_full_history_after_end_to_end_flow(self):
        User = get_user_model()
        owner = User.objects.create_user(
            username="workitem_export_owner",
            email="workitem_export_owner@example.com",
            password="pw",
        )
        project = Project.objects.create(name="WorkItem Export Project", owner=owner)
        work_item = WorkItem.create_minimal(project=project)

        work_item.append_seed_revision("Seed baseline", owner, "Initial proposal")
        work_item.lock_seed(1)
        work_item.set_phase(WorkItem.PHASE_EXECUTE)
        generate_codex_instruction(work_item)
        finalise_work_item(work_item)

        self.client.force_login(owner)
        resp = self.client.get(reverse("projects:work_item_export", args=[project.id]))
        self.assertEqual(resp.status_code, 200)

        payload = json.loads(resp.content.decode("utf-8"))
        data = payload.get("work_item") or {}
        self.assertTrue(isinstance(data.get("seed_log"), list))
        self.assertTrue(isinstance(data.get("deliverables"), list))
        self.assertTrue(isinstance(data.get("activity_log"), list))
        self.assertGreaterEqual(len(data.get("seed_log") or []), 1)
        self.assertGreaterEqual(len(data.get("deliverables") or []), 1)

        actions = [str(row.get("action") or "") for row in (data.get("activity_log") or []) if isinstance(row, dict)]
        self.assertIn("seed_proposed", actions)
        self.assertIn("seed_locked", actions)
        self.assertIn("phase_changed", actions)
        self.assertIn("deliverable_generated", actions)
        self.assertIn("work_item_finalised", actions)

