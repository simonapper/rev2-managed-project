from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch
import json

from projects.models import Project, WorkItem


class DeraxModeTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="derax_owner",
            email="derax_owner@example.com",
            password="pw",
        )

    def test_project_workflow_mode_defaults_to_pde(self):
        project = Project.objects.create(name="DERAX Default Project", owner=self.owner)
        self.assertEqual(project.workflow_mode, Project.WorkflowMode.PDE)

    def test_derax_home_creates_primary_work_item(self):
        project = Project.objects.create(
            name="DERAX Home Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse("projects:derax_project_home", args=[project.id]))
        self.assertEqual(response.status_code, 200)

        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        self.assertIsNotNone(work_item)
        self.assertEqual(work_item.active_phase, WorkItem.PHASE_DEFINE)

    def test_project_home_redirects_to_derax_when_workflow_mode_derax(self):
        project = Project.objects.create(
            name="DERAX Redirect Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse("accounts:project_home", args=[project.id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("projects:derax_project_home", args=[project.id]),
        )

    def test_derax_home_define_end_in_mind_saves_to_work_item(self):
        project = Project.objects.create(
            name="DERAX Define Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])

        response = self.client.post(
            url,
            {
                "action": "save_end_in_mind",
                "end_in_mind": "Define a clear and testable DERAX endpoint.",
            },
        )
        self.assertEqual(response.status_code, 302)

        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        self.assertIsNotNone(work_item)
        self.assertEqual(work_item.intent_raw, "Define a clear and testable DERAX endpoint.")

    @patch("projects.views_derax.generate_text", return_value="Scope:\n- Example define response")
    def test_derax_home_define_llm_turn_records_history(self, _mock_generate_text):
        project = Project.objects.create(
            name="DERAX Define LLM Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])

        response = self.client.post(
            url,
            {
                "action": "define_llm_turn",
                "define_user_input": "Help me define the end state for this work item.",
            },
        )
        self.assertEqual(response.status_code, 302)

        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        self.assertIsNotNone(work_item)
        history = list(work_item.derax_define_history or [])
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].get("role"), "user")
        self.assertEqual(history[1].get("role"), "assistant")

    @patch(
        "projects.views_derax.generate_text",
        side_effect=[
            "Unstructured response",
            (
                "Scope:\n"
                "- What we are defining now:\n"
                "- What we are NOT doing yet:\n\n"
                "Intent (user-owned, 1-2 sentences):\n"
                "- Clarified intent.\n\n"
                "Candidate outcomes (choose one, max 3 bullets):\n"
                "- Outcome A:\n"
                "- Outcome B:\n"
                "- Outcome C:\n\n"
                "Unknowns that change outcomes (max 5 bullets):\n"
                "- Unknown one.\n\n"
                "Assumptions (only if unavoidable, max 3 bullets):\n"
                "- None.\n\n"
                "One decisive next question:\n"
                "- What must be true for success?\n\n"
                "Source basis:\n"
                "- Based only on the user's message(s) in this chat.\n\n"
                "Confidence:\n"
                "- Medium - enough clarity for next turn."
            ),
        ],
    )
    def test_define_turn_requests_correction_when_headers_missing(self, mock_generate_text):
        project = Project.objects.create(
            name="DERAX Define Correction Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])

        response = self.client.post(
            url,
            {
                "action": "define_llm_turn",
                "define_user_input": "Clarify my end state.",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mock_generate_text.call_count, 2)

    @patch("projects.views_derax.generate_text", return_value="Scope:\n- Focused define response")
    def test_define_turn_ajax_returns_history_and_latest_text(self, _mock_generate_text):
        project = Project.objects.create(
            name="DERAX Define Ajax Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])

        response = self.client.post(
            url,
            {
                "action": "define_llm_turn",
                "define_user_input": "Clarify the end in mind.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode("utf-8"))
        self.assertTrue(payload.get("ok"))
        self.assertIn("history_html", payload)
        self.assertIn("latest_define_assistant_text", payload)

    def test_derax_home_end_in_mind_autosave_ajax(self):
        project = Project.objects.create(
            name="DERAX Autosave Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])

        response = self.client.post(
            url,
            {
                "action": "autosave_end_in_mind",
                "end_in_mind": "Autosaved end in mind text.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)

        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        self.assertIsNotNone(work_item)
        self.assertEqual(work_item.intent_raw, "Autosaved end in mind text.")

    def test_use_define_response_as_intent(self):
        project = Project.objects.create(
            name="DERAX Use Intent Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])
        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        if work_item is None:
            self.client.get(url)
            work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        work_item.derax_define_history = [
            {"role": "assistant", "text": "Candidate intent from DEFINE.", "timestamp": "2026-02-23T00:00:00Z"}
        ]
        work_item.save(update_fields=["derax_define_history", "updated_at"])

        response = self.client.post(
            url,
            {
                "action": "use_define_response_as_intent",
                "candidate_text": "Candidate intent from DEFINE.",
            },
        )
        self.assertEqual(response.status_code, 302)
        work_item.refresh_from_db()
        self.assertEqual(work_item.intent_raw, "Candidate intent from DEFINE.")

    def test_lock_define_and_move_to_explore_adds_seed_history(self):
        project = Project.objects.create(
            name="DERAX Lock Define Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])
        self.client.post(
            url,
            {
                "action": "save_end_in_mind",
                "end_in_mind": "Locked define intent text.",
            },
        )

        response = self.client.post(url, {"action": "lock_define_and_explore"})
        self.assertEqual(response.status_code, 302)

        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        self.assertIsNotNone(work_item)
        self.assertEqual(work_item.active_phase, WorkItem.PHASE_EXPLORE)
        self.assertEqual(len(list(work_item.seed_log or [])), 1)
        first = dict((work_item.seed_log or [])[0] or {})
        self.assertEqual(first.get("seed_text"), "Locked define intent text.")
        self.assertEqual(first.get("reason"), "DEFINE_LOCKED")
