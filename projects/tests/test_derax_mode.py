from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch
import json

from django.core.files.base import ContentFile

from projects.models import Project, WorkItem
from projects.models import ProjectDocument

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

    @patch(
        "projects.views_derax.generate_text",
        return_value=json.dumps(
            {
                "phase": "DEFINE",
                "headline": "Define headline",
                "core": {
                    "end_in_mind": "Example define response",
                    "destination_conditions": [],
                    "non_goals": [],
                    "adjacent_angles": [],
                    "assumptions": [],
                    "ambiguities": [],
                    "risks": [],
                    "scope_changes": [],
                },
                "parked": [],
                "footnotes": [],
                "next": {"recommended_phase": "DEFINE", "one_question": "Q?"},
                "meta": {"work_item_id": "1", "project_id": 1, "chat_id": None, "created_at": "2026-02-24T00:00:00Z"},
            }
        ),
    )
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
        self.assertIn('"phase": "DEFINE"', str(history[1].get("text") or ""))

    @patch(
        "projects.views_derax.generate_text",
        side_effect=[
            "Unstructured response",
            (
                '{'
                '"phase":"DEFINE","headline":"h","core":{"end_in_mind":"Clarified destination","destination_conditions":["Condition one"],'
                '"non_goals":[],"adjacent_angles":[],"assumptions":[],"ambiguities":["Ambiguity one"],"risks":[],"scope_changes":[]},'
                '"parked":["Route detail parked"],"footnotes":[],"next":{"recommended_phase":"DEFINE","one_question":"What is the primary outcome signal?"},'
                '"meta":{"work_item_id":"1","project_id":1,"chat_id":null,"created_at":"2026-02-24T00:00:00Z"}'
                '}'
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

    @patch(
        "projects.views_derax.generate_text",
        return_value='{"phase":"DEFINE","headline":"h","core":{"end_in_mind":"Focused define response","destination_conditions":[],"non_goals":[],"adjacent_angles":[],"assumptions":[],"ambiguities":[],"risks":[],"scope_changes":[]},"parked":[],"footnotes":[],"next":{"recommended_phase":"DEFINE","one_question":"q"},"meta":{"work_item_id":"1","project_id":1,"chat_id":null,"created_at":"2026-02-24T00:00:00Z"}}',
    )
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
        self.assertEqual(payload.get("latest_define_assistant_text"), "Focused define response")

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

    @patch(
        "projects.views_derax.generate_text",
        return_value='{"phase":"EXPLORE","headline":"h","core":{"end_in_mind":"Restated destination","destination_conditions":[],"non_goals":[],"adjacent_angles":["Angle"],"assumptions":["Assumption"],"ambiguities":[],"risks":["Risk"],"scope_changes":[]},"parked":[],"footnotes":[],"next":{"recommended_phase":"EXPLORE","one_question":"q"},"meta":{"work_item_id":"1","project_id":1,"chat_id":null,"created_at":"2026-02-24T00:00:00Z"}}',
    )
    def test_explore_turn_ajax_records_explore_history(self, _mock_generate_text):
        project = Project.objects.create(
            name="DERAX Explore Ajax Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])
        self.client.post(url, {"action": "save_end_in_mind", "end_in_mind": "Destination text"})
        self.client.post(url, {"action": "lock_define_and_explore"})

        response = self.client.post(
            url,
            {"action": "explore_llm_turn", "phase_user_input": "Pressure test this destination."},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        history = list(work_item.derax_explore_history or [])
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].get("role"), "user")
        self.assertEqual(history[1].get("role"), "assistant")

    def test_lock_explore_and_move_to_refine(self):
        project = Project.objects.create(
            name="DERAX Lock Explore Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])
        self.client.post(url, {"action": "save_end_in_mind", "end_in_mind": "Destination text"})
        self.client.post(url, {"action": "lock_define_and_explore"})
        self.client.post(url, {"action": "save_end_in_mind", "end_in_mind": "Explore-adjusted destination"})

        response = self.client.post(url, {"action": "lock_explore_and_refine"})
        self.assertEqual(response.status_code, 302)
        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        self.assertEqual(work_item.active_phase, WorkItem.PHASE_REFINE)
        self.assertEqual(len(list(work_item.seed_log or [])), 2)
        last = dict((work_item.seed_log or [])[1] or {})
        self.assertEqual(last.get("reason"), "EXPLORE_LOCKED")

    def test_lock_refine_moves_to_approve(self):
        project = Project.objects.create(
            name="DERAX Lock Refine Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])
        self.client.post(url, {"action": "save_end_in_mind", "end_in_mind": "Define destination"})
        self.client.post(url, {"action": "lock_define_and_explore"})
        self.client.post(url, {"action": "save_end_in_mind", "end_in_mind": "Explore destination"})
        self.client.post(url, {"action": "lock_explore_and_refine"})
        self.client.post(url, {"action": "autosave_refine_input", "refine_input": "Refined destination"})

        response = self.client.post(url, {"action": "lock_refine_stage", "refine_input": "Refined destination"})
        self.assertEqual(response.status_code, 302)
        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        self.assertEqual(work_item.active_phase, WorkItem.PHASE_APPROVE)
        last = dict((work_item.seed_log or [])[-1] or {})
        self.assertEqual(last.get("reason"), "REFINE_LOCKED")

    def test_return_to_define_from_explore_preserves_histories(self):
        project = Project.objects.create(
            name="DERAX Return Define Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])
        self.client.get(url)
        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        self.assertIsNotNone(work_item)

        work_item.derax_define_history = [
            {"role": "user", "text": "Define input", "timestamp": "2026-02-24T10:00:00Z"},
            {"role": "assistant", "text": "Define output", "timestamp": "2026-02-24T10:00:05Z"},
        ]
        work_item.derax_explore_history = [
            {"role": "user", "text": "Explore input", "timestamp": "2026-02-24T10:05:00Z"},
            {"role": "assistant", "text": "Explore output", "timestamp": "2026-02-24T10:05:05Z"},
        ]
        work_item.active_phase = WorkItem.PHASE_EXPLORE
        work_item.save(update_fields=["derax_define_history", "derax_explore_history", "active_phase", "updated_at"])

        response = self.client.post(url, {"action": "return_to_define"})
        self.assertEqual(response.status_code, 302)

        work_item.refresh_from_db()
        self.assertEqual(work_item.active_phase, WorkItem.PHASE_DEFINE)
        self.assertEqual(len(list(work_item.derax_define_history or [])), 2)
        self.assertEqual(len(list(work_item.derax_explore_history or [])), 2)

    @patch(
        "projects.views_derax.generate_text",
        return_value='{"phase":"DEFINE","headline":"h","core":{"end_in_mind":"Persisted destination","destination_conditions":[],"non_goals":[],"adjacent_angles":[],"assumptions":[],"ambiguities":[],"risks":[],"scope_changes":[]},"parked":[],"footnotes":[],"next":{"recommended_phase":"DEFINE","one_question":"q"},"meta":{"work_item_id":"1","project_id":1,"chat_id":null,"created_at":"2026-02-24T00:00:00Z"}}',
    )
    def test_define_turn_persists_derax_json_artefact(self, _mock_generate_text):
        project = Project.objects.create(
            name="DERAX Persist Run Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])
        response = self.client.post(
            url,
            {"action": "define_llm_turn", "phase_user_input": "Persist this run."},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        runs = list(work_item.derax_runs or [])
        self.assertGreaterEqual(len(runs), 1)
        asset_id = int(runs[-1].get("asset_id") or 0)
        self.assertTrue(ProjectDocument.objects.filter(id=asset_id, project=project).exists())

    def test_generate_derax_audit_creates_project_file(self):
        project = Project.objects.create(
            name="Hatfield Strategy",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])
        self.client.get(url)
        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        self.assertIsNotNone(work_item)
        work_item.append_seed_revision("Define outcome text", self.owner, "DEFINE_LOCKED")
        work_item.lock_seed(1)
        work_item.append_activity(actor=self.owner, action="phase_changed", notes="DEFINE -> EXPLORE")

        response = self.client.post(url, {"action": "generate_derax_audit"})
        self.assertEqual(response.status_code, 302)

        doc = ProjectDocument.objects.filter(project=project, original_name__contains="-DERAX-Audit.txt").order_by("-id").first()
        self.assertIsNotNone(doc)
        self.assertIn("Hatfield-Strategy-DERAX-Audit.txt", str(doc.original_name))
        doc.file.open("rb")
        try:
            body = doc.file.read().decode("utf-8", errors="ignore")
        finally:
            doc.file.close()
        self.assertIn("# DERAX Project Audit", body)
        self.assertIn("# Seed log", body)
        self.assertIn("DEFINE_LOCKED", body)

    def test_export_latest_derax_draft_creates_project_document(self):
        project = Project.objects.create(
            name="DERAX Export Draft Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])
        self.client.get(url)
        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        self.assertIsNotNone(work_item)
        work_item.derax_define_history = [
            {
                "role": "assistant",
                "text": json.dumps(
                    {
                        "meta": {
                            "tko_id": "tko_test",
                            "derax_version": "1.0",
                            "phase": "DEFINE",
                            "timestamp": "2026-02-24T00:00:00Z",
                            "source_chat_id": "",
                            "source_turn_id": "",
                        },
                        "canonical_summary": "",
                        "intent": {
                            "destination": "Export destination",
                            "success_criteria": ["One"],
                            "constraints": ["Two"],
                            "non_goals": ["Three"],
                            "assumptions": [],
                            "open_questions": [],
                        },
                        "explore": {"adjacent_ideas": [], "risks": [], "tradeoffs": [], "reframes": []},
                        "parked_for_later": {"items": []},
                        "artefacts": {"proposed": [], "generated": []},
                        "validation": {"schema_ok": "", "errors": []},
                    }
                ),
                "timestamp": "2026-02-24T00:00:01Z",
            }
        ]
        work_item.save(update_fields=["derax_define_history", "updated_at"])

        response = self.client.post(url, {"action": "export_latest_derax_draft"})
        self.assertEqual(response.status_code, 302)
        doc = ProjectDocument.objects.filter(project=project, original_name__contains="-DERAX-DEFINE-Editable-").order_by("-id").first()
        self.assertIsNotNone(doc)
        self.assertTrue(str(doc.original_name).lower().endswith(".odt"))
        self.assertIn(f"/projects/{project.id}/documents/{doc.id}/edit/", response["Location"])

    def test_import_derax_from_document_adds_define_history(self):
        project = Project.objects.create(
            name="DERAX Import Draft Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])
        self.client.get(url)
        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        self.assertIsNotNone(work_item)

        draft = (
            "# DERAX Editable Draft\n\n"
            "## Phase\n\n"
            "DEFINE\n\n"
            "## End in mind\n\n"
            "Imported destination\n\n"
            "## Success criteria\n\n"
            "- A clear outcome\n\n"
            "## Constraints\n\n"
            "- No route yet\n\n"
            "## Non-goals\n\n"
            "- No execution plan\n\n"
            "## Open questions\n\n"
            "- What is in scope?\n\n"
            "## Adjacent ideas\n\n"
            "- \n\n"
            "## Risks\n\n"
            "- \n\n"
            "## Trade-offs\n\n"
            "- \n\n"
            "## Reframes\n\n"
            "- \n\n"
            "## Parked for later\n\n"
            "- Route planning\n"
        )
        doc = ProjectDocument(
            project=project,
            title="Import draft",
            original_name="Test-DERAX-DEFINE-Editable.txt",
            content_type="text/markdown",
            size_bytes=len(draft.encode("utf-8")),
            uploaded_by=self.owner,
        )
        doc.file.save(f"derax/{work_item.id}/Test-DERAX-DEFINE-Editable.txt", ContentFile(draft.encode("utf-8")), save=False)
        doc.save()

        response = self.client.post(url, {"action": "import_derax_from_document", "import_doc_id": str(doc.id)})
        self.assertEqual(response.status_code, 302)
        work_item.refresh_from_db()
        self.assertEqual(work_item.intent_raw, "Imported destination")
        self.assertGreaterEqual(len(list(work_item.derax_define_history or [])), 2)
