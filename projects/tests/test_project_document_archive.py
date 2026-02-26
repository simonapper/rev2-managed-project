from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse

from projects.models import AuditLog, Project, ProjectDocument


class ProjectDocumentArchiveTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="doc_owner",
            email="doc_owner@example.com",
            password="pw",
        )
        self.project = Project.objects.create(
            name="Doc Archive Project",
            owner=self.owner,
            purpose="archive tests",
            kind=Project.Kind.STANDARD,
        )
        self.doc = ProjectDocument.objects.create(
            project=self.project,
            title="Draft",
            original_name="draft.odt",
            content_type="application/vnd.oasis.opendocument.text",
            size_bytes=5,
            uploaded_by=self.owner,
        )
        self.doc.file.save("draft.odt", ContentFile(b"hello"), save=True)
        self.url = reverse("accounts:project_config_info", args=[self.project.id])

    def test_owner_archives_document(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url, {"action": "project_doc_archive", "doc_id": str(self.doc.id)})
        self.assertEqual(response.status_code, 302)
        self.doc.refresh_from_db()
        self.assertTrue(self.doc.is_archived)
        self.assertIsNotNone(self.doc.archived_at)
        self.assertEqual(self.doc.archived_by_id, self.owner.id)
        self.assertTrue(
            AuditLog.objects.filter(
                project=self.project,
                actor=self.owner,
                event_type="PROJECT_DOC_ARCHIVED",
                entity_type="ProjectDocument",
                entity_id=str(self.doc.id),
            ).exists()
        )

    def test_non_admin_cannot_hard_delete(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url, {"action": "project_doc_delete", "doc_id": str(self.doc.id)})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ProjectDocument.objects.filter(id=self.doc.id).exists())

    def test_staff_can_hard_delete(self):
        self.owner.is_staff = True
        self.owner.save(update_fields=["is_staff"])
        self.client.force_login(self.owner)
        response = self.client.post(self.url, {"action": "project_doc_delete", "doc_id": str(self.doc.id)})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProjectDocument.objects.filter(id=self.doc.id).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                project=self.project,
                actor=self.owner,
                event_type="PROJECT_DOC_HARD_DELETED",
                entity_type="ProjectDocument",
                entity_id=str(self.doc.id),
            ).exists()
        )
