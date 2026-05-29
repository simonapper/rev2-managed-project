import os
from unittest.mock import Mock, patch
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase

from chats.services import llm
from config.models import SystemConfigPointers


class LLMProviderTests(TestCase):
    def test_provider_resolution_prefers_user_setting_over_env(self):
        User = get_user_model()
        user = User.objects.create_user(username="u1", email="u1@example.com", password="pw")
        user.profile.llm_provider = "copilot"
        user.profile.save(update_fields=["llm_provider"])

        with patch.dict(os.environ, {"LLM_PROVIDER": "openai"}):
            provider = llm._resolve_provider(user=user)

        self.assertEqual(provider, "copilot")

    def test_copilot_adapter_is_called_when_selected(self):
        mock_agent = Mock()
        mock_result = Mock()
        mock_result.text = "copilot reply"
        mock_agent.run.return_value = mock_result

        with patch("chats.services.llm._get_copilot_agent", return_value=mock_agent):
            out = llm.generate_text(
                system_blocks=["System rule"],
                messages=[{"role": "user", "content": "Hello"}],
                provider="copilot",
            )

        self.assertEqual(out, "copilot reply")
        mock_agent.run.assert_called_once()

    def test_anthropic_adapter_is_called_when_selected(self):
        mock_client = Mock()
        mock_client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="anthropic reply")]
        )

        with patch("chats.services.llm._get_anthropic_client", return_value=mock_client):
            out = llm.generate_text(
                system_blocks=["System rule"],
                messages=[{"role": "user", "content": "Hello"}],
                provider="anthropic",
            )

        self.assertEqual(out, "anthropic reply")
        mock_client.messages.create.assert_called_once()

    def test_deepseek_adapter_is_called_when_selected(self):
        mock_client = Mock()
        mock_client.chat_completion.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="deepseek reply"))]
        )

        with patch("chats.services.llm._get_deepseek_client", return_value=mock_client):
            out = llm.generate_text(
                system_blocks=["System rule"],
                messages=[{"role": "user", "content": "Hello"}],
                provider="deepseek",
            )

        self.assertEqual(out, "deepseek reply")
        mock_client.chat_completion.assert_called_once()

    def test_openai_model_uses_user_profile_default(self):
        User = get_user_model()
        user = User.objects.create_user(username="u4", email="u4@example.com", password="pw")
        user.profile.openai_model_default = "gpt-5-mini"
        user.profile.save(update_fields=["openai_model_default"])

        mock_client = Mock()
        mock_client.responses.create.return_value = SimpleNamespace(output_text="ok")

        with patch("chats.services.llm._get_openai_client", return_value=mock_client):
            llm.generate_text(
                system_blocks=["Rule"],
                messages=[{"role": "user", "content": "Hello"}],
                user=user,
            )

        call_kwargs = mock_client.responses.create.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "gpt-5-mini")

    def test_openai_default_model_falls_back_to_gpt_5_5(self):
        User = get_user_model()
        user = User.objects.create_user(username="u5", email="u5@example.com", password="pw")
        user.profile.openai_model_default = ""
        user.profile.save(update_fields=["openai_model_default"])
        SystemConfigPointers.objects.all().delete()

        self.assertEqual(llm._get_default_model_key(user=user), "gpt-5.5")

    def test_gemini_default_model_falls_back_to_3_5_flash(self):
        User = get_user_model()
        user = User.objects.create_user(username="u6", email="u6@example.com", password="pw")
        user.profile.gemini_model_default = ""
        user.profile.save(update_fields=["gemini_model_default"])
        SystemConfigPointers.objects.all().delete()

        self.assertEqual(llm._get_default_gemini_model_key(user=user), "gemini-3.5-flash")

    def test_gemini_ssl_context_uses_custom_google_ca_bundle_when_set(self):
        with patch.dict(os.environ, {"GOOGLE_API_CA_BUNDLE": "C:/ca/google.pem"}):
            with patch("chats.services.llm.ssl.create_default_context") as mock_context:
                llm._get_gemini_ssl_context()

        mock_context.assert_called_once_with(cafile="C:/ca/google.pem")

    def test_anthropic_panes_parses_fenced_json_and_structured_fields(self):
        fenced_json = """```json
{
  "answer": "Using Claude Sonnet.",
  "key_info": ["Model: Claude Sonnet", "Provider: Anthropic"],
  "visuals": {"model_hierarchy": "Claude 4.5 -> Sonnet"},
  "reasoning": "Reasoning summary.",
  "output": "claude-sonnet-4-5"
}
```"""
        mock_client = Mock()
        mock_client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="text", text=fenced_json)]
        )

        with patch("chats.services.llm._get_anthropic_client", return_value=mock_client):
            panes = llm.generate_panes(
                user_text="Who are you?",
                provider="anthropic",
            )

        self.assertEqual(panes["answer"], "Using Claude Sonnet.")
        self.assertEqual(panes["reasoning"], "Reasoning summary.")
        self.assertIn("- Model: Claude Sonnet", panes["key_info"])
        self.assertIn("- Provider: Anthropic", panes["key_info"])
        self.assertIn('"model_hierarchy"', panes["visuals"])
        self.assertIn("Claude 4.5 -> Sonnet", panes["visuals"])

    def test_anthropic_panes_recovers_json_like_payload_with_multiline_strings(self):
        malformed_payload = """{
  "answer": "Yes, there are UK options.",
  "key_info": [
    "Angels exist",
    "Grants exist"
  ],
  "visuals": [
    "Option A -> angels",
    "Option B -> grants"
  ],
  "reasoning": "Funding can help but does not replace an operating partner.",
  "output": "## UK options

- Angels
- Grants
"
}"""
        mock_client = Mock()
        mock_client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="text", text=malformed_payload)]
        )

        with patch("chats.services.llm._get_anthropic_client", return_value=mock_client):
            panes = llm.generate_panes(
                user_text="Any UK options?",
                provider="anthropic",
            )

        self.assertEqual(panes["answer"], "Yes, there are UK options.")
        self.assertIn("- Angels exist", panes["key_info"])
        self.assertIn("- Option A -> angels", panes["visuals"])
        self.assertIn("Funding can help", panes["reasoning"])
        self.assertIn("## UK options", panes["output"])

    def test_gemini_panes_uses_3_5_flash_and_parses_json_output(self):
        User = get_user_model()
        user = User.objects.create_user(username="u7", email="u7@example.com", password="pw")
        user.profile.gemini_model_default = ""
        user.profile.save(update_fields=["gemini_model_default"])
        SystemConfigPointers.objects.all().delete()

        gemini_payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": """{
  "answer": "Gemini JSON OK.",
  "key_info": ["Provider: Gemini", "Model: gemini-3.5-flash"],
  "visuals": {"flow": "request -> json"},
  "reasoning": "Parsed as structured panes.",
  "output": "Ready"
}"""
                            }
                        ]
                    }
                }
            ]
        }

        with patch("chats.services.llm._gemini_generate_content", return_value=gemini_payload) as mock_call:
            panes = llm.generate_panes(
                user_text="Return JSON panes.",
                provider="gemini",
                user=user,
            )

        self.assertEqual(mock_call.call_args.kwargs["model"], "gemini-3.5-flash")
        self.assertEqual(panes["answer"], "Gemini JSON OK.")
        self.assertIn("- Provider: Gemini", panes["key_info"])
        self.assertIn("- Model: gemini-3.5-flash", panes["key_info"])
        self.assertIn('"flow"', panes["visuals"])
        self.assertEqual(panes["reasoning"], "Parsed as structured panes.")
        self.assertEqual(panes["output"], "Ready")
