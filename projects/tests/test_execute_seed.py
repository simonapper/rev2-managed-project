from django.contrib.auth import get_user_model
from django.test import TestCase

from projects.models import Project, ProjectAnchor
from projects.services_artefacts import seed_execute_from_route
from projects.services_execute import seed_execute_from_route as seed_exec_anchor


class ExecuteSeedTests(TestCase):
    def test_execute_seed_preserves_stage_id(self):
        route = {
            "stages": [
                {"stage_number": 1, "stage_id": "S1", "title": "One"},
                {"stage_number": 2, "stage_id": "S2", "title": "Two"},
            ]
        }
        out = seed_execute_from_route(route)
        self.assertEqual(out["stages"][0]["stage_id"], "S1")
        self.assertEqual(out["stages"][1]["stage_id"], "S2")
        self.assertEqual(out["current_stage_id"], "S1")

    def test_execute_anchor_seeded_once(self):
        User = get_user_model()
        owner = User.objects.create_user(username="owner", password="pw")
        project = Project.objects.create(name="P1", owner=owner)
        route_payload = {"stages": [{"stage_number": 1, "stage_id": "S1", "title": "One"}]}
        ProjectAnchor.objects.create(
            project=project,
            marker="ROUTE",
            content_json=route_payload,
            content="",
        )
        exec_anchor = seed_exec_anchor(project)
        self.assertIsNotNone(exec_anchor)
        exec_anchor.content_json["overall_status"] = "paused"
        exec_anchor.save(update_fields=["content_json"])
        exec_anchor2 = seed_exec_anchor(project)
        self.assertEqual(exec_anchor2.content_json["overall_status"], "paused")
