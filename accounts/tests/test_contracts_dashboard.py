import tempfile
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from chats.models import ContractText
from chats.services.contracts.texts import resolve_contract_text


@override_settings(MEDIA_ROOT=tempfile.gettempdir())
class ContractsDashboardTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="dash_u", email="dash_u@example.com", password="pw")
        self.client.force_login(self.user)

    def test_dashboard_loads_and_lists_keys(self):
        response = self.client.get(reverse("accounts:system_contracts_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Contracts Dashboard")
        self.assertContains(response, "language")
        self.assertContains(response, "phase.refine")
        self.assertContains(response, "Verification &amp; Approval")
        self.assertContains(response, "pde.validator.boilerplate")

    def test_dashboard_ajax_select_returns_selected_panel(self):
        response = self.client.get(
            reverse("accounts:system_contracts_dashboard"),
            {"ajax": "1", "key": "tone"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode("utf-8"))
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("selected_key"), "tone")
        self.assertIn("User override", str(payload.get("selected_html") or ""))
        self.assertIn("Other presets", str(payload.get("selected_html") or ""))

    def test_reset_reverts_effective_to_default(self):
        ContractText.objects.create(
            key="tone",
            scope_type=ContractText.ScopeType.GLOBAL_DEFAULT,
            scope_id=None,
            status=ContractText.Status.ACTIVE,
            text="Default tone",
            updated_by=self.user,
        )
        ContractText.objects.create(
            key="tone",
            scope_type=ContractText.ScopeType.USER,
            scope_id=self.user.id,
            status=ContractText.Status.ACTIVE,
            text="User tone",
            updated_by=self.user,
        )
        self.client.post(
            reverse("accounts:system_contracts_dashboard"),
            {"action": "reset_user", "key": "tone"},
        )
        resolved = resolve_contract_text(self.user, "tone")
        self.assertEqual(resolved["effective_source"], "DEFAULT")
        self.assertEqual(resolved["effective_text"], "Default tone")

    def test_export_then_import_reproduces_overrides(self):
        ContractText.objects.create(
            key="tone",
            scope_type=ContractText.ScopeType.USER,
            scope_id=self.user.id,
            status=ContractText.Status.ACTIVE,
            text="User tone export",
            updated_by=self.user,
        )
        ContractText.objects.create(
            key="reasoning",
            scope_type=ContractText.ScopeType.USER,
            scope_id=self.user.id,
            status=ContractText.Status.ACTIVE,
            text="User reasoning export",
            updated_by=self.user,
        )

        export_response = self.client.get(reverse("accounts:system_contract_pack_export"))
        self.assertEqual(export_response.status_code, 200)
        payload = json.loads(export_response.content.decode("utf-8"))
        self.assertEqual(payload.get("pack_type"), "ContractPack")
        self.assertEqual(len(payload.get("contracts") or []), 2)

        User = get_user_model()
        user2 = User.objects.create_user(username="dash_u2", email="dash_u2@example.com", password="pw")
        self.client.force_login(user2)
        upload = SimpleUploadedFile("contract_pack.json", export_response.content, content_type="application/json")
        import_response = self.client.post(
            reverse("accounts:system_contract_pack_import"),
            {"contract_pack": upload},
        )
        self.assertEqual(import_response.status_code, 302)

        resolved_tone = resolve_contract_text(user2, "tone")
        resolved_reasoning = resolve_contract_text(user2, "reasoning")
        self.assertEqual(resolved_tone["effective_text"], "User tone export")
        self.assertEqual(resolved_tone["effective_source"], "USER")
        self.assertEqual(resolved_reasoning["effective_text"], "User reasoning export")
        self.assertEqual(resolved_reasoning["effective_source"], "USER")

    def test_export_selected_single_contract(self):
        ContractText.objects.create(
            key="tone",
            scope_type=ContractText.ScopeType.USER,
            scope_id=self.user.id,
            status=ContractText.Status.ACTIVE,
            text="Tone only",
            updated_by=self.user,
        )
        ContractText.objects.create(
            key="reasoning",
            scope_type=ContractText.ScopeType.USER,
            scope_id=self.user.id,
            status=ContractText.Status.ACTIVE,
            text="Reasoning other",
            updated_by=self.user,
        )

        response = self.client.post(
            reverse("accounts:system_contract_pack_export"),
            {"keys": ["tone"]},
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode("utf-8"))
        contracts = payload.get("contracts") or []
        self.assertEqual(len(contracts), 1)
        self.assertEqual(contracts[0].get("key"), "tone")
        self.assertEqual(contracts[0].get("text"), "Tone only")

    @patch("accounts.views_system.generate_text", return_value="Preview output text")
    def test_preview_contract_ajax_returns_text_without_saving(self, mock_generate_text):
        response = self.client.post(
            reverse("accounts:system_contracts_dashboard"),
            {
                "action": "preview_contract",
                "key": "tone",
                "preview_prompt": "Assess the situation between the UK and Europe.",
                "preview_contract_text": "Use concise and practical language.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode("utf-8"))
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("preview_text"), "Preview output text")
        self.assertEqual(ContractText.objects.count(), 0)
        self.assertTrue(mock_generate_text.called)
