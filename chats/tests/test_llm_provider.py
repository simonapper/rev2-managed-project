import os
from unittest.mock import Mock, patch
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase

from chats.services import llm


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
        user.profile.openai_model_default = "gpt-4.1-mini"
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
        self.assertEqual(call_kwargs["model"], "gpt-4.1-mini")

    def test_anthropic_panes_parses_fenced_json_and_structured_fields(self):
        fenced_json = """```json
{
  "answer": "Using Claude Sonnet.",
  "key_info": ["Model: Claude Sonnet", "Provider: Anthropic"],
  "visuals": {"model_hierarchy": "Claude 4.5 -> Sonnet"},
  "reasoning": "Reasoning summary.",
  "output": "claude-sonnet-4-5-20250929"
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
