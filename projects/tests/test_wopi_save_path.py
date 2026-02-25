from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from projects.models import Project, ProjectDocument
from projects.views_project import _handle_wopi_put_override


class WopiSavePathTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="wopi_owner",
            email="wopi_owner@example.com",
            password="pw",
        )
        self.project = Project.objects.create(
            name="WOPI Save Path Project",
            owner=self.owner,
        )

    def test_put_override_does_not_duplicate_upload_prefix(self):
        doc = ProjectDocument(
            project=self.project,
            title="Test",
            original_name="Test.txt",
            content_type="text/plain",
            size_bytes=0,
            uploaded_by=self.owner,
        )
        doc.file.save("derax/123/Test.txt", ContentFile(b"first"), save=False)
        doc.save()

        original_name = str(doc.file.name)
        self.assertIn(f"projects/{self.project.id}/documents/", original_name)

        resp = _handle_wopi_put_override(doc=doc, lock_value="", raw=b"second")
        self.assertEqual(resp.status_code, 200)
        doc.refresh_from_db()
        first_update_name = str(doc.file.name)
        self.assertTrue(first_update_name.startswith(f"projects/{self.project.id}/documents/derax/123/"))
        self.assertEqual(first_update_name.count(f"projects/{self.project.id}/documents/"), 1)

        resp = _handle_wopi_put_override(doc=doc, lock_value="", raw=b"third")
        self.assertEqual(resp.status_code, 200)
        doc.refresh_from_db()
        second_update_name = str(doc.file.name)
        self.assertTrue(second_update_name.startswith(f"projects/{self.project.id}/documents/derax/123/"))
        self.assertEqual(second_update_name.count(f"projects/{self.project.id}/documents/"), 1)
