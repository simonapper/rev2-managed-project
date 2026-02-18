from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from projects.models import Project
from projects.views_project import _IMPORT_RATE_LIMIT_MAX


class ProjectSecurityHardeningTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="sec_user", email="sec_user@example.com", password="pw")
        self.project = Project.objects.create(
            name="Security Project",
            owner=self.user,
            purpose="Security test",
            kind=Project.Kind.STANDARD,
            primary_type=Project.PrimaryType.DELIVERY,
            mode=Project.Mode.PLAN,
            status=Project.Status.ACTIVE,
        )
        self.client.force_login(self.user)

    def test_active_project_set_blocks_external_next_redirect(self):
        resp = self.client.post(
            reverse("accounts:active_project_set"),
            {
                "project_id": str(self.project.id),
                "next": "https://evil.example/phish",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("accounts:dashboard"))

    def test_project_import_is_rate_limited(self):
        url = reverse("accounts:project_import")
        response = None
        for _ in range(_IMPORT_RATE_LIMIT_MAX + 1):
            response = self.client.post(url, {})
        self.assertIsNotNone(response)
        msgs = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("Too many import attempts" in m for m in msgs))
