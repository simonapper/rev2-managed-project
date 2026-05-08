from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from chats.models import ChatWorkspace
from projects.models import Project


class ChatCreateAjaxTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="chat_create_ajax_user",
            email="chat_create_ajax_user@example.com",
            password="pw",
        )
        self.project = Project.objects.create(
            name="SANDBOX Project",
            owner=self.user,
        )
        self.client.force_login(self.user)

    def test_chat_create_ajax_returns_redirect_url(self):
        chat = ChatWorkspace.objects.create(
            project=self.project,
            title="New sandbox chat",
            created_by=self.user,
            status=ChatWorkspace.Status.ACTIVE,
        )

        with patch("accounts.views.bootstrap_chat", return_value=(chat, {})):
            response = self.client.post(
                reverse("accounts:chat_create"),
                {
                    "ajax": "1",
                    "title": "New sandbox chat",
                    "project": str(self.project.id),
                    "cde_mode": "SKIP",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["chat_id"], chat.id)
        self.assertEqual(payload["redirect_url"], reverse("accounts:chat_detail", args=[chat.id]))

    @patch("accounts.views.validate_cde_inputs")
    def test_chat_create_ajax_returns_feedback_html_for_controlled_blocker(self, mock_validate):
        mock_validate.return_value = {
            "ok": False,
            "first_blocker": {
                "field_key": "chat.goal",
                "verdict": "revise",
                "issues": ["Goal is too broad."],
                "questions": ["What is the stopping condition?"],
                "suggested_revision": "Produce a scoped answer with a stopping condition.",
                "debug_system_blocks": ["System block"],
                "debug_user_text": "User text",
            },
        }

        response = self.client.post(
            reverse("accounts:chat_create"),
            {
                "ajax": "1",
                "title": "Controlled sandbox chat",
                "project": str(self.project.id),
                "cde_mode": "CONTROLLED",
                "chat_goal": "Help me with everything",
                "chat_success": "",
                "chat_constraints": "",
                "chat_non_goals": "",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "CDE needs revision.")
        self.assertIn("CDE needs revision", payload["feedback_html"])
        self.assertEqual(ChatWorkspace.objects.count(), 0)
