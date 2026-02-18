from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from accounts.views import _IMPORT_RATE_LIMIT_MAX
from projects.models import Project


class ChatImportRateLimitTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="chat_imp_user", email="chat_imp_user@example.com", password="pw")
        self.project = Project.objects.create(
            name="Chat Import Project",
            owner=self.user,
            purpose="Rate limit test",
            kind=Project.Kind.STANDARD,
            primary_type=Project.PrimaryType.DELIVERY,
            mode=Project.Mode.PLAN,
            status=Project.Status.ACTIVE,
        )
        self.client.force_login(self.user)

    def test_chat_import_is_rate_limited(self):
        url = reverse("accounts:chat_import")
        response = None
        for _ in range(_IMPORT_RATE_LIMIT_MAX + 1):
            response = self.client.post(url, {"project_id": str(self.project.id)})
        self.assertIsNotNone(response)
        msgs = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("Too many import attempts" in m for m in msgs))
