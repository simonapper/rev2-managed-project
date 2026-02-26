from django.contrib.auth import get_user_model
from django.test import TestCase

from chats.services.derax.compile import compile_derax_to_cko, persist_compiled_cko
from chats.services.derax.persist import persist_derax_payload
from chats.services.derax.schema import empty_derax_payload
from projects.models import Project, ProjectDocument, WorkItem


class DeraxCompilePersistTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="derax_store_u",
            email="derax_store_u@example.com",
            password="pw",
        )
        self.project = Project.objects.create(
            name="Derax Persist Project",
            owner=self.user,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        self.work_item = WorkItem.create_minimal(
            project=self.project,
            active_phase=WorkItem.PHASE_DEFINE,
            title="Derax Work Item",
        )

    def test_persist_derax_payload_saves_file_and_links_run(self):
        payload = empty_derax_payload(
            "DEFINE",
            {"work_item_id": str(self.work_item.id), "project_id": self.project.id, "chat_id": None},
        )
        payload["core"]["end_in_mind"] = "Clear destination"
        doc = persist_derax_payload(work_item=self.work_item, payload=payload, user=self.user, chat=None)
        self.assertIsNotNone(doc.id)
        self.work_item.refresh_from_db()
        runs = list(self.work_item.derax_runs or [])
        self.assertEqual(len(runs), 1)
        self.assertEqual(int(runs[0]["asset_id"]), int(doc.id))
        self.assertTrue(ProjectDocument.objects.filter(id=doc.id, project=self.project).exists())

    def test_compile_generates_markdown_with_footnotes_and_provenance(self):
        payload = empty_derax_payload(
            "EXPLORE",
            {"work_item_id": str(self.work_item.id), "project_id": self.project.id, "chat_id": None},
        )
        payload["headline"] = "Explore headline"
        payload["core"]["end_in_mind"] = "Stable destination"
        payload["core"]["destination_conditions"] = ["Cond A"]
        payload["core"]["non_goals"] = ["Non-goal A"]
        payload["core"]["assumptions"] = ["Assumption A"]
        payload["core"]["ambiguities"] = ["Ambiguity A"]
        payload["core"]["risks"] = ["Risk A"]
        payload["parked"] = ["Parked A"]
        payload["footnotes"] = ["Footnote A"]
        persist_derax_payload(work_item=self.work_item, payload=payload, user=self.user, chat=None)
        md = compile_derax_to_cko(self.work_item)
        self.assertIn("# Canonical Summary", md)
        self.assertIn("# Footnotes", md)
        self.assertIn("# Provenance", md)
        self.assertIn("Parked A", md)
        doc = persist_compiled_cko(self.work_item, user=self.user)
        self.assertTrue(ProjectDocument.objects.filter(id=doc.id, project=self.project).exists())
        self.assertTrue(str(doc.original_name or "").lower().endswith(".docx"))
        self.assertEqual(
            str(doc.content_type or ""),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
