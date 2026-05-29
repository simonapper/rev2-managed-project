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

    def test_chat_create_page_keeps_pde_option_available_before_project_choice(self):
        response = self.client.get(reverse("accounts:chat_create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<option value="pde" data-requires-workflow="PDE">PDE/CDE</option>', html=True)

    def test_chat_create_ajax_derax_path_redirects_to_derax_flow_without_title(self):
        response = self.client.post(
            reverse("accounts:chat_create"),
            {
                "ajax": "1",
                "project": str(self.project.id),
                "path_mode": "derax",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["redirect_url"], reverse("projects:derax_project_home", args=[self.project.id]))
        self.assertEqual(ChatWorkspace.objects.count(), 0)

    def test_chat_create_ajax_pde_path_redirects_to_pde_flow_without_title(self):
        response = self.client.post(
            reverse("accounts:chat_create"),
            {
                "ajax": "1",
                "project": str(self.project.id),
                "path_mode": "pde",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["redirect_url"], reverse("projects:pde_detail", args=[self.project.id]))
        self.assertEqual(ChatWorkspace.objects.count(), 0)

    def test_chat_create_ajax_rejects_pde_path_for_non_pde_project(self):
        derax_project = Project.objects.create(
            name="DERAX project",
            owner=self.user,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )

        response = self.client.post(
            reverse("accounts:chat_create"),
            {
                "ajax": "1",
                "project": str(derax_project.id),
                "path_mode": "pde",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "PDE/CDE is only available for PDE projects.")
        self.assertEqual(ChatWorkspace.objects.count(), 0)

    def test_chat_create_ajax_sandbox_path_allows_undefined_managed_project(self):
        managed_project = Project.objects.create(
            name="Managed undefined project",
            owner=self.user,
            kind=Project.Kind.STANDARD,
            workflow_mode=Project.WorkflowMode.PDE,
        )
        chat = ChatWorkspace.objects.create(
            project=managed_project,
            title="Undefined project sandbox chat",
            created_by=self.user,
            status=ChatWorkspace.Status.ACTIVE,
        )

        with patch("accounts.views.bootstrap_chat", return_value=(chat, {})) as mock_bootstrap:
            response = self.client.post(
                reverse("accounts:chat_create"),
                {
                    "ajax": "1",
                    "title": "Undefined project sandbox chat",
                    "project": str(managed_project.id),
                    "path_mode": "sandbox",
                    "cde_mode": "SKIP",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["chat_id"], chat.id)
        self.assertTrue(mock_bootstrap.call_args.kwargs["skip_readiness_checks"])
        self.assertEqual(mock_bootstrap.call_args.kwargs["cde_mode"], "SKIP")
