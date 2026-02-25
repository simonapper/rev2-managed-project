from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from chats.models import ChatMessage, ChatWorkspace
from projects.models import Project


class DeraxChatUiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="derax_chat_ui_user",
            email="derax_chat_ui_user@example.com",
            password="pw",
        )
        self.project = Project.objects.create(
            name="Derax Chat UI Project",
            owner=self.user,
        )
        self.chat = ChatWorkspace.objects.create(
            project=self.project,
            title="DERAX chat",
            created_by=self.user,
            status=ChatWorkspace.Status.ACTIVE,
        )
        self.client.force_login(self.user)

    def test_derax_toggle_sets_flag(self):
        url = reverse("accounts:derax_toggle", args=[self.chat.id])
        response = self.client.post(url, {"enabled": "1"})
        self.assertEqual(response.status_code, 302)
        self.chat.refresh_from_db()
        self.assertTrue(self.chat.derax_enabled)

    @patch("accounts.views.generate_derax")
    def test_derax_run_calls_generate_derax_via_mock_and_redirects(self, mock_generate_derax):
        self.chat.derax_enabled = True
        self.chat.save(update_fields=["derax_enabled", "updated_at"])
        mock_generate_derax.return_value = {
            "payload": {"meta": {"phase": "DEFINE"}, "intent": {"destination": "Outcome"}},
            "json_artefact_id": "123",
        }
        url = reverse("accounts:derax_run", args=[self.chat.id])
        response = self.client.post(
            url,
            {
                "content": "Define my destination",
                "derax_phase": "DEFINE",
            },
        )
        self.assertEqual(response.status_code, 302)
        mock_generate_derax.assert_called_once()
        self.assertEqual(ChatMessage.objects.filter(chat=self.chat, role=ChatMessage.Role.USER).count(), 1)
        self.assertEqual(ChatMessage.objects.filter(chat=self.chat, role=ChatMessage.Role.ASSISTANT).count(), 1)

    @patch("accounts.views.compile_derax_chat_run_to_cko_artefact", return_value="987")
    def test_derax_compile_calls_compiler_via_mock_and_redirects(self, mock_compile):
        url = reverse("accounts:derax_compile", args=[self.chat.id])
        response = self.client.post(url, {})
        self.assertEqual(response.status_code, 302)
        mock_compile.assert_called_once()
        session = self.client.session
        self.assertEqual(str(session.get("derax_last_compiled_artefact_id") or ""), "987")

