import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from chats.services.derax.compile import compile_derax_run_to_cko_markdown
from chats.services.derax.persist import persist_derax_payload
from chats.services.derax.schema import empty_payload
from projects.models import Project, ProjectDocument


class DeraxV1CompilePersistTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="derax_v1_user",
            email="derax_v1_user@example.com",
            password="pw",
        )
        self.project = Project.objects.create(
            name="Derax V1 Persist Project",
            owner=self.user,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )

    def test_persist_derax_payload_roundtrip(self):
        payload = empty_payload(phase="DEFINE")
        payload["intent"]["destination"] = "Launch DERAX v1"
        payload["intent"]["success_criteria"] = ["Schema and rules implemented"]
        payload["intent"]["constraints"] = ["No LLM integration in this slice"]
        payload["intent"]["non_goals"] = ["No UI drawers yet"]

        artefact_id = persist_derax_payload(
            project_id=self.project.id,
            chat_id=123,
            turn_id="turn-001",
            phase="DEFINE",
            payload=payload,
            raw_text=json.dumps(payload),
            user_id=self.user.id,
        )
        doc = ProjectDocument.objects.get(id=int(artefact_id))
        doc.file.open("rb")
        try:
            body = doc.file.read().decode("utf-8")
        finally:
            doc.file.close()
        loaded = json.loads(body)
        self.assertEqual(loaded["intent"]["destination"], "Launch DERAX v1")

    def test_compile_derax_run_to_cko_markdown_includes_footnotes(self):
        payload = empty_payload(phase="EXPLORE")
        payload["canonical_summary"] = "Short summary"
        payload["parked_for_later"]["items"] = [{"title": "Ops detail", "detail": "Park for later"}]
        markdown = compile_derax_run_to_cko_markdown([payload])
        self.assertIn("## Footnotes", markdown)
        self.assertIn("Ops detail: Park for later", markdown)
