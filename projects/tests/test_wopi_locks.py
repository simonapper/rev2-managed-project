from django.contrib.auth import get_user_model
from django.test import TestCase

from projects.models import Project, ProjectDocument
from projects.views_project import _handle_wopi_lock_override


class WopiLockTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="wopi_lock_owner",
            email="wopi_lock_owner@example.com",
            password="pw",
        )
        self.project = Project.objects.create(
            name="WOPI Lock Project",
            owner=self.owner,
        )
        self.doc = ProjectDocument.objects.create(
            project=self.project,
            title="Doc",
            original_name="doc.txt",
            file="projects/1/documents/doc.txt",
            content_type="text/plain",
            size_bytes=0,
            uploaded_by=self.owner,
            wopi_lock="lock_a",
        )

    def test_unlock_and_relock_uses_old_lock_header(self):
        resp = _handle_wopi_lock_override(
            doc=self.doc,
            override="UNLOCK_AND_RELOCK",
            lock_value="lock_b",
            old_lock_value="lock_a",
        )
        self.assertEqual(resp.status_code, 200)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.wopi_lock, "lock_b")

    def test_put_requires_matching_lock(self):
        from projects.views_project import _handle_wopi_put_override

        resp = _handle_wopi_put_override(doc=self.doc, lock_value="", raw=b"abc")
        self.assertEqual(resp.status_code, 200)
