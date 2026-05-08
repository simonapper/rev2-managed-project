from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from chats.models import ChatMessage, ChatWorkspace
from config.models import SystemConfigPointers
from projects.models import Project


class ChatDetailAjaxTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="chat_detail_ajax_user",
            email="chat_detail_ajax_user@example.com",
            password="pw",
        )
        self.project = Project.objects.create(
            name="Chat Detail Ajax Project",
            owner=self.user,
        )
        self.chat = ChatWorkspace.objects.create(
            project=self.project,
            title="Ajax chat",
            created_by=self.user,
            status=ChatWorkspace.Status.ACTIVE,
        )
        SystemConfigPointers.objects.create(id=1, updated_by=self.user)
        self.client.force_login(self.user)

    @patch("accounts.views.generate_panes")
    def test_chat_message_create_ajax_returns_redirect_url(self, mock_generate_panes):
        mock_generate_panes.return_value = {
            "answer": "Answer text",
            "reasoning": "",
            "output": "",
        }

        response = self.client.post(
            reverse("accounts:chat_message_create"),
            {
                "chat_id": str(self.chat.id),
                "content": "Hello",
                "ajax": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "redirect_url": reverse("accounts:chat_detail", args=[self.chat.id]),
            },
        )
        self.assertEqual(ChatMessage.objects.filter(chat=self.chat, role=ChatMessage.Role.USER).count(), 1)
        self.assertEqual(ChatMessage.objects.filter(chat=self.chat, role=ChatMessage.Role.ASSISTANT).count(), 1)

    @patch("accounts.views.generate_derax")
    def test_derax_run_ajax_returns_redirect_url(self, mock_generate_derax):
        self.chat.derax_enabled = True
        self.chat.save(update_fields=["derax_enabled", "updated_at"])
        mock_generate_derax.return_value = {
            "payload": {"meta": {"phase": "DEFINE"}, "intent": {"destination": "Outcome"}},
            "json_artefact_id": "123",
        }

        response = self.client.post(
            reverse("accounts:derax_run", args=[self.chat.id]),
            {
                "content": "Define my destination",
                "derax_phase": "DEFINE",
                "ajax": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "redirect_url": reverse("accounts:chat_detail", args=[self.chat.id]),
            },
        )
        self.assertEqual(ChatMessage.objects.filter(chat=self.chat, role=ChatMessage.Role.USER).count(), 1)
        self.assertEqual(ChatMessage.objects.filter(chat=self.chat, role=ChatMessage.Role.ASSISTANT).count(), 1)
