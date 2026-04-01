from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch
import json

from django.core.files.base import ContentFile

from projects.models import Project, WorkItem
from projects.models import ProjectDocument
from chats.models import ContractText

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

    def test_explore_phase_input_text_uses_end_in_mind_line_from_readable_response(self):
        project = Project.objects.create(
            name="DERAX Explore Input Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        response = self.client.get(reverse("projects:derax_project_home", args=[project.id]))
        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        self.assertIsNotNone(work_item)
        work_item.active_phase = WorkItem.PHASE_EXPLORE
        work_item.intent_raw = (
            "Phase: EXPLORE\n"
            "End in mind: Destination text\n"
            "Adjacent ideas:\n"
            "- Placeholder\n"
        )
        work_item.save(update_fields=["active_phase", "intent_raw", "updated_at"])

        response = self.client.get(reverse("projects:derax_project_home", args=[project.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["phase_input_text"], "Destination text")

    def test_phase_input_text_prefers_latest_user_text_for_define_and_explore(self):
        project = Project.objects.create(
            name="DERAX Phase Input User Text Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        self.client.get(reverse("projects:derax_project_home", args=[project.id]))
        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        self.assertIsNotNone(work_item)

        work_item.active_phase = WorkItem.PHASE_DEFINE
        work_item.intent_raw = "Fallback destination"
        work_item.derax_define_history = [
            {"role": "user", "text": "Define prompt text", "timestamp": "2026-04-01T10:00:00Z"},
            {"role": "assistant", "text": "{\"meta\":{\"phase\":\"DEFINE\"},\"intent\":{\"destination\":\"Fallback destination\",\"success_criteria\":[],\"constraints\":[],\"non_goals\":[],\"assumptions\":[],\"open_questions\":[]},\"explore\":{\"adjacent_ideas\":[],\"risks\":[],\"tradeoffs\":[],\"reframes\":[]},\"parked_for_later\":{\"items\":[]},\"artefacts\":{\"proposed\":[],\"generated\":[],\"requirements\":{},\"intake\":{}},\"validation\":{\"schema_ok\":\"\",\"errors\":[]}}", "timestamp": "2026-04-01T10:00:01Z"},
        ]
        work_item.save(update_fields=["active_phase", "intent_raw", "derax_define_history", "updated_at"])

        response = self.client.get(reverse("projects:derax_project_home", args=[project.id]))
        self.assertEqual(response.context["phase_input_text"], "Define prompt text")

        work_item.active_phase = WorkItem.PHASE_EXPLORE
        work_item.derax_explore_history = [
            {"role": "user", "text": "Explore prompt text", "timestamp": "2026-04-01T10:01:00Z"},
            {"role": "assistant", "text": "{\"meta\":{\"phase\":\"EXPLORE\"},\"intent\":{\"destination\":\"Fallback destination\",\"success_criteria\":[],\"constraints\":[],\"non_goals\":[],\"assumptions\":[],\"open_questions\":[]},\"explore\":{\"adjacent_ideas\":[\"A\"],\"risks\":[\"B\"],\"tradeoffs\":[\"C\"],\"reframes\":[\"D\"]},\"parked_for_later\":{\"items\":[]},\"artefacts\":{\"proposed\":[],\"generated\":[],\"requirements\":{},\"intake\":{}},\"validation\":{\"schema_ok\":\"\",\"errors\":[]}}", "timestamp": "2026-04-01T10:01:01Z"},
        ]
        work_item.save(update_fields=["active_phase", "derax_explore_history", "updated_at"])

        response = self.client.get(reverse("projects:derax_project_home", args=[project.id]))
        self.assertEqual(response.context["phase_input_text"], "Explore prompt text")

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

    def test_project_create_can_select_derax_workflow(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("accounts:project_create"),
            {
                "name": "DERAX Created Project",
                "purpose": "Create directly in DERAX mode",
                "project_flow": "DERAX",
                "kind": Project.Kind.STANDARD,
                "primary_type": Project.PrimaryType.DELIVERY,
                "mode": Project.Mode.PLAN,
            },
        )
        self.assertEqual(response.status_code, 302)
        project = Project.objects.get(name="DERAX Created Project")
        self.assertEqual(project.workflow_mode, Project.WorkflowMode.DERAX_WORK)
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

    @patch(
        "projects.views_derax.generate_text",
        return_value=json.dumps(
            {
                "meta": {
                    "tko_id": "tko_test",
                    "derax_version": "1.0",
                    "phase": "DEFINE",
                    "timestamp": "2026-02-24T00:00:00Z",
                    "source_chat_id": "",
                    "source_turn_id": "",
                },
                "canonical_summary": "Event planning cadence",
                "intent": {
                    "destination": "Define a KPI-led event planning cadence.",
                    "success_criteria": [],
                    "constraints": [],
                    "non_goals": [],
                    "assumptions": [],
                    "open_questions": ["Which event types matter most?"],
                },
                "explore": {
                    "adjacent_ideas": ["Monthly, quarterly, annual horizons"],
                    "risks": [],
                    "tradeoffs": [],
                    "reframes": [],
                },
                "parked_for_later": {"items": []},
                "artefacts": {
                    "proposed": [],
                    "generated": [],
                    "requirements": {},
                    "intake": {},
                },
                "validation": {"schema_ok": True, "errors": []},
            }
        ),
    )
    def test_define_turn_recovers_parseable_payload_that_breaks_define_policy(self, _mock_generate_text):
        project = Project.objects.create(
            name="DERAX Define Recovery Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])

        response = self.client.post(
            url,
            {
                "action": "define_llm_turn",
                "define_user_input": "Set up event cadence KPIs.",
            },
        )
        self.assertEqual(response.status_code, 302)

        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        self.assertIsNotNone(work_item)
        history = list(work_item.derax_define_history or [])
        self.assertEqual(len(history), 2)
        payload = json.loads(str(history[1].get("text") or "{}"))
        self.assertEqual(payload.get("meta", {}).get("phase"), "DEFINE")
        self.assertEqual((payload.get("explore") or {}).get("adjacent_ideas"), [])
        self.assertEqual((payload.get("artefacts") or {}).get("proposed"), [])
        parked_items = list((payload.get("parked_for_later") or {}).get("items") or [])
        self.assertGreaterEqual(len(parked_items), 1)
        parked_titles = [str(dict(row or {}).get("title") or "") for row in parked_items if isinstance(row, dict)]
        self.assertTrue(any("parked" in title.lower() for title in parked_titles))

    @patch(
        "projects.views_derax.generate_text",
        side_effect=[
            "Not JSON at all",
            json.dumps(
                {
                    "meta": {"phase": "DEFINE"},
                    "intent": {
                        "destination": "",
                        "success_criteria": [],
                        "constraints": [],
                        "non_goals": [],
                        "assumptions": [],
                        "open_questions": [],
                    },
                    "explore": {"adjacent_ideas": [], "risks": [], "tradeoffs": [], "reframes": []},
                    "parked_for_later": {"items": []},
                    "artefacts": {"proposed": [], "generated": [], "requirements": {}, "intake": {}},
                    "validation": {"schema_ok": "", "errors": []},
                }
            ),
        ],
    )
    def test_define_turn_uses_local_fallback_when_destination_missing(self, mock_generate_text):
        project = Project.objects.create(
            name="DERAX Define Local Fallback Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])
        user_text = (
            "Define a closed-loop KPI, objectives, strategies, and tactics framework spanning monthly, quarterly, "
            "and annual horizons. The company is adding retail sales to online only. This is a cash drain."
        )

        response = self.client.post(
            url,
            {
                "action": "define_llm_turn",
                "phase_user_input": user_text,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mock_generate_text.call_count, 2)
        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        history = list(work_item.derax_define_history or [])
        payload = json.loads(str(history[-1].get("text") or "{}"))
        self.assertTrue((payload.get("intent") or {}).get("destination"))
        self.assertEqual((payload.get("meta") or {}).get("phase"), "DEFINE")
        self.assertTrue((payload.get("intent") or {}).get("open_questions"))

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

    def test_phase_contract_save_ajax_creates_project_user_override(self):
        project = Project.objects.create(
            name="DERAX Contract Save Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])

        response = self.client.post(
            url,
            {
                "action": "save_phase_contract_text",
                "contract_phase": "DEFINE",
                "contract_text": "Custom DEFINE contract text.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode("utf-8"))
        self.assertTrue(payload.get("ok"))

        row = ContractText.objects.filter(
            key="phase.define",
            scope_type=ContractText.ScopeType.PROJECT_USER,
            scope_project_id=project.id,
            scope_user_id=self.owner.id,
            status=ContractText.Status.ACTIVE,
        ).first()
        self.assertIsNotNone(row)
        self.assertEqual(str(row.text or ""), "Custom DEFINE contract text.")

    def test_phase_contract_default_ajax_retires_project_user_override(self):
        project = Project.objects.create(
            name="DERAX Contract Reset Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])
        ContractText.objects.create(
            key="phase.define",
            scope_type=ContractText.ScopeType.PROJECT_USER,
            scope_project_id=project.id,
            scope_user_id=self.owner.id,
            status=ContractText.Status.ACTIVE,
            text="Temporary override",
            updated_by=self.owner,
        )

        response = self.client.post(
            url,
            {
                "action": "reset_phase_contract_text",
                "contract_phase": "DEFINE",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode("utf-8"))
        self.assertTrue(payload.get("ok"))
        self.assertEqual(str(payload.get("source") or ""), "DEFAULT")

        active_row = ContractText.objects.filter(
            key="phase.define",
            scope_type=ContractText.ScopeType.PROJECT_USER,
            scope_project_id=project.id,
            scope_user_id=self.owner.id,
            status=ContractText.Status.ACTIVE,
        ).first()
        self.assertIsNone(active_row)

    def test_derax_file_archive_action_archives_document(self):
        project = Project.objects.create(
            name="DERAX File Archive Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])
        doc = ProjectDocument.objects.create(
            project=project,
            title="Archive me",
            original_name="archive_me.odt",
            content_type="application/vnd.oasis.opendocument.text",
            size_bytes=5,
            uploaded_by=self.owner,
        )
        doc.file.save("archive_me.odt", ContentFile(b"hello"), save=True)

        response = self.client.post(url, {"action": "project_doc_archive", "doc_id": str(doc.id)})
        self.assertEqual(response.status_code, 302)
        doc.refresh_from_db()
        self.assertTrue(doc.is_archived)
        self.assertEqual(doc.archived_by_id, self.owner.id)

    def test_derax_file_delete_action_requires_admin(self):
        project = Project.objects.create(
            name="DERAX File Delete Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])
        doc = ProjectDocument.objects.create(
            project=project,
            title="Do not delete",
            original_name="keep_me.odt",
            content_type="application/vnd.oasis.opendocument.text",
            size_bytes=5,
            uploaded_by=self.owner,
        )
        doc.file.save("keep_me.odt", ContentFile(b"hello"), save=True)

        response = self.client.post(url, {"action": "project_doc_delete", "doc_id": str(doc.id)})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ProjectDocument.objects.filter(id=doc.id).exists())

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

    def test_lock_define_uses_latest_define_response_when_intent_blank(self):
        project = Project.objects.create(
            name="DERAX Lock Define Fallback Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])
        self.client.get(url)
        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        self.assertIsNotNone(work_item)
        work_item.intent_raw = ""
        work_item.derax_define_history = [
            {
                "role": "assistant",
                "text": json.dumps(
                    {
                        "meta": {"phase": "DEFINE"},
                        "intent": {"destination": "Intent from DEFINE payload"},
                        "explore": {},
                        "parked_for_later": {"items": []},
                        "artefacts": {"proposed": [], "generated": []},
                        "validation": {"schema_ok": "", "errors": []},
                        "canonical_summary": "",
                    }
                ),
                "timestamp": "2026-02-24T10:00:00Z",
            }
        ]
        work_item.save(update_fields=["intent_raw", "derax_define_history", "updated_at"])

        response = self.client.post(url, {"action": "lock_define_and_explore"})
        self.assertEqual(response.status_code, 302)

        work_item.refresh_from_db()
        self.assertEqual(work_item.active_phase, WorkItem.PHASE_EXPLORE)
        self.assertEqual(work_item.intent_raw, "Intent from DEFINE payload")
        self.assertEqual(len(list(work_item.seed_log or [])), 1)
        first = dict((work_item.seed_log or [])[0] or {})
        self.assertEqual(first.get("seed_text"), "Intent from DEFINE payload")
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

    @patch(
        "projects.views_derax.generate_text",
        return_value=json.dumps(
            {
                "meta": {
                    "tko_id": "tko_test",
                    "derax_version": "1.0",
                    "phase": "EXPLORE",
                    "timestamp": "2026-02-24T00:00:00Z",
                    "source_chat_id": "",
                    "source_turn_id": "",
                },
                "canonical_summary": "",
                "intent": {
                    "destination": "",
                    "success_criteria": [],
                    "constraints": [],
                    "non_goals": [],
                    "assumptions": [],
                    "open_questions": [],
                },
                "explore": {
                    "adjacent_ideas": [],
                    "risks": [],
                    "tradeoffs": [],
                    "reframes": [],
                },
                "parked_for_later": {"items": []},
                "artefacts": {
                    "proposed": [],
                    "generated": [],
                    "requirements": {},
                    "intake": {},
                },
                "validation": {"schema_ok": "", "errors": []},
            }
        ),
    )
    def test_explore_turn_backfills_empty_payload_from_define_destination(self, _mock_generate_text):
        project = Project.objects.create(
            name="DERAX Explore Recovery Project",
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
        payload = json.loads(str(history[-1].get("text") or "{}"))
        self.assertEqual((payload.get("intent") or {}).get("destination"), "Destination text")
        self.assertTrue((payload.get("explore") or {}).get("tradeoffs"))
        self.assertTrue((payload.get("explore") or {}).get("reframes"))

    @patch(
        "projects.views_derax.generate_text",
        side_effect=[
            json.dumps(
                {
                    "meta": {
                        "tko_id": "tko_test",
                        "derax_version": "1.0",
                        "phase": "EXPLORE",
                        "timestamp": "2026-02-24T00:00:00Z",
                        "source_chat_id": "",
                        "source_turn_id": "",
                    },
                    "canonical_summary": "",
                    "intent": {
                        "destination": "",
                        "success_criteria": [],
                        "constraints": [],
                        "non_goals": [],
                        "assumptions": [],
                        "open_questions": [],
                    },
                    "explore": {
                        "adjacent_ideas": [],
                        "risks": [],
                        "tradeoffs": [],
                        "reframes": [],
                    },
                    "parked_for_later": {"items": []},
                    "artefacts": {
                        "proposed": [],
                        "generated": [],
                        "requirements": {},
                        "intake": {},
                    },
                    "validation": {"schema_ok": "", "errors": []},
                }
            ),
            json.dumps(
                {
                    "meta": {
                        "tko_id": "tko_test",
                        "derax_version": "1.0",
                        "phase": "EXPLORE",
                        "timestamp": "2026-02-24T00:00:01Z",
                        "source_chat_id": "",
                        "source_turn_id": "",
                    },
                    "canonical_summary": "",
                    "intent": {
                        "destination": "Destination text",
                        "success_criteria": [],
                        "constraints": [],
                        "non_goals": [],
                        "assumptions": [],
                        "open_questions": [],
                    },
                    "explore": {
                        "adjacent_ideas": ["Add a rolling assumptions review."],
                        "risks": ["Teams may optimise only for near-term metrics."],
                        "tradeoffs": ["More review cadence means more coordination time."],
                        "reframes": ["Treat the system as a learning loop, not a fixed scorecard."],
                    },
                    "parked_for_later": {"items": []},
                    "artefacts": {
                        "proposed": [],
                        "generated": [],
                        "requirements": {},
                        "intake": {},
                    },
                    "validation": {"schema_ok": "", "errors": []},
                }
            ),
        ],
    )
    def test_explore_turn_retries_when_only_placeholder_content_is_recovered(self, mock_generate_text):
        project = Project.objects.create(
            name="DERAX Explore Retry Project",
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
        self.assertEqual(mock_generate_text.call_count, 2)
        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        history = list(work_item.derax_explore_history or [])
        payload = json.loads(str(history[-1].get("text") or "{}"))
        self.assertEqual((payload.get("explore") or {}).get("adjacent_ideas"), ["Add a rolling assumptions review."])
        self.assertEqual((payload.get("explore") or {}).get("reframes"), ["Treat the system as a learning loop, not a fixed scorecard."])

    @patch(
        "projects.views_derax.generate_text",
        side_effect=[
            json.dumps(
                {
                    "meta": {
                        "tko_id": "tko_test",
                        "derax_version": "1.0",
                        "phase": "EXPLORE",
                        "timestamp": "2026-02-24T00:00:00Z",
                        "source_chat_id": "",
                        "source_turn_id": "",
                    },
                    "canonical_summary": "",
                    "intent": {
                        "destination": "",
                        "success_criteria": [],
                        "constraints": [],
                        "non_goals": [],
                        "assumptions": [],
                        "open_questions": [],
                    },
                    "explore": {
                        "adjacent_ideas": [],
                        "risks": [],
                        "tradeoffs": [],
                        "reframes": [],
                    },
                    "parked_for_later": {"items": []},
                    "artefacts": {
                        "proposed": [],
                        "generated": [],
                        "requirements": {},
                        "intake": {},
                    },
                    "validation": {"schema_ok": "", "errors": []},
                }
            ),
            json.dumps(
                {
                    "meta": {
                        "tko_id": "tko_test",
                        "derax_version": "1.0",
                        "phase": "EXPLORE",
                        "timestamp": "2026-02-24T00:00:01Z",
                        "source_chat_id": "",
                        "source_turn_id": "",
                    },
                    "canonical_summary": "",
                    "intent": {
                        "destination": "",
                        "success_criteria": [],
                        "constraints": [],
                        "non_goals": [],
                        "assumptions": [],
                        "open_questions": [],
                    },
                    "explore": {
                        "adjacent_ideas": [],
                        "risks": [],
                        "tradeoffs": [],
                        "reframes": [],
                    },
                    "parked_for_later": {"items": []},
                    "artefacts": {
                        "proposed": [],
                        "generated": [],
                        "requirements": {},
                        "intake": {},
                    },
                    "validation": {"schema_ok": "", "errors": []},
                }
            ),
        ],
    )
    def test_explore_turn_uses_local_backfill_when_retry_stays_placeholder_only(self, mock_generate_text):
        project = Project.objects.create(
            name="DERAX Explore Local Backfill Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])
        destination = (
            "Define a closed-loop KPI, objectives, strategies, and tactics framework spanning monthly, "
            "quarterly, and annual horizons that uses each monthly review to capture actuals, diagnose variances, "
            "recalibrate forward targets, and commit to next-month tactics."
        )
        self.client.post(url, {"action": "save_end_in_mind", "end_in_mind": destination})
        self.client.post(url, {"action": "lock_define_and_explore"})

        response = self.client.post(
            url,
            {"action": "explore_llm_turn", "phase_user_input": "Pressure test the KPI review cadence and target resets."},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_generate_text.call_count, 2)
        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        history = list(work_item.derax_explore_history or [])
        payload = json.loads(str(history[-1].get("text") or "{}"))
        self.assertEqual((payload.get("intent") or {}).get("destination"), destination)
        self.assertNotIn("not yet surfaced", " ".join((payload.get("explore") or {}).get("adjacent_ideas") or []).lower())
        self.assertTrue(any("review" in item.lower() or "horizon" in item.lower() for item in ((payload.get("explore") or {}).get("adjacent_ideas") or [])))
        self.assertTrue(any("target" in item.lower() or "kpi" in item.lower() for item in ((payload.get("explore") or {}).get("risks") or [])))

    @patch(
        "projects.views_derax.generate_text",
        side_effect=[
            json.dumps(
                {
                    "meta": {"phase": "EXPLORE"},
                    "intent": {"destination": "", "success_criteria": [], "constraints": [], "non_goals": [], "assumptions": [], "open_questions": []},
                    "explore": {"adjacent_ideas": [], "risks": [], "tradeoffs": [], "reframes": []},
                    "parked_for_later": {"items": []},
                    "artefacts": {"proposed": [], "generated": [], "requirements": {}, "intake": {}},
                    "validation": {"schema_ok": "", "errors": []},
                }
            ),
            json.dumps(
                {
                    "meta": {"phase": "EXPLORE"},
                    "intent": {"destination": "Destination text", "success_criteria": [], "constraints": [], "non_goals": [], "assumptions": [], "open_questions": []},
                    "explore": {
                        "adjacent_ideas": ["Adjacent angle not yet surfaced"],
                        "risks": ["Risk not yet surfaced"],
                        "tradeoffs": ["Trade-off not yet surfaced"],
                        "reframes": ["Reframe not yet surfaced"],
                    },
                    "parked_for_later": {"items": []},
                    "artefacts": {"proposed": [], "generated": [], "requirements": {}, "intake": {}},
                    "validation": {"schema_ok": "", "errors": []},
                }
            ),
        ],
    )
    def test_explore_turn_local_backfill_handles_placeholder_variants(self, mock_generate_text):
        project = Project.objects.create(
            name="DERAX Explore Placeholder Variant Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])
        self.client.post(url, {"action": "save_end_in_mind", "end_in_mind": "Destination text"})
        self.client.post(url, {"action": "lock_define_and_explore"})

        response = self.client.post(
            url,
            {"action": "explore_llm_turn", "phase_user_input": "Pressure test the KPI review cadence and target resets."},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_generate_text.call_count, 2)
        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        payload = json.loads(str(list(work_item.derax_explore_history or [])[-1].get("text") or "{}"))
        self.assertFalse(any("not yet surfaced" in item.lower() for item in ((payload.get("explore") or {}).get("adjacent_ideas") or [])))

    @patch(
        "projects.views_derax.generate_text",
        side_effect=[
            "Not JSON at all",
            json.dumps(
                {
                    "meta": {"phase": "EXPLORE"},
                    "intent": {
                        "destination": "Destination text",
                        "success_criteria": [],
                        "constraints": [],
                        "non_goals": [],
                        "assumptions": [],
                        "open_questions": [],
                    },
                    "explore": {
                        "adjacent_ideas": ["Adjacent angle not yet surfaced."],
                        "risks": ["Risk not yet surfaced."],
                        "tradeoffs": ["Trade-off not yet surfaced."],
                        "reframes": ["Reframe not yet surfaced."],
                    },
                    "parked_for_later": {"items": []},
                    "artefacts": {"proposed": [], "generated": [], "requirements": {}, "intake": {}},
                    "validation": {"schema_ok": "", "errors": []},
                }
            ),
        ],
    )
    def test_explore_turn_finalises_placeholder_payload_after_correction(self, mock_generate_text):
        project = Project.objects.create(
            name="DERAX Explore Correction Finalise Project",
            owner=self.owner,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.client.force_login(self.owner)
        url = reverse("projects:derax_project_home", args=[project.id])
        self.client.post(url, {"action": "save_end_in_mind", "end_in_mind": "Destination text"})
        self.client.post(url, {"action": "lock_define_and_explore"})

        response = self.client.post(
            url,
            {"action": "explore_llm_turn", "phase_user_input": "Pressure test the KPI review cadence and target resets."},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_generate_text.call_count, 2)
        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        payload = json.loads(str(list(work_item.derax_explore_history or [])[-1].get("text") or "{}"))
        self.assertFalse(any("not yet surfaced" in item.lower() for item in ((payload.get("explore") or {}).get("adjacent_ideas") or [])))
        self.assertTrue((payload.get("explore") or {}).get("risks"))

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
