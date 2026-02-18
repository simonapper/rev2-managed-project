from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class UserLLMProviderSettingTests(TestCase):
    def test_user_cannot_change_llm_provider_to_copilot(self):
        User = get_user_model()
        user = User.objects.create_user(username="u1", email="u1@example.com", password="pw")

        self.client.force_login(user)
        profile = user.profile

        response = self.client.post(
            reverse("accounts:user_config_user"),
            data={
                "default_language": profile.default_language,
                "default_language_variant": profile.default_language_variant,
                "language_switching_permitted": "on",
                "persist_language_switch_for_session": "on",
                "summary_rollup_trigger_message_count": str(profile.summary_rollup_trigger_message_count),
                "llm_provider": "copilot",
            },
        )

        self.assertEqual(response.status_code, 200)
        profile.refresh_from_db()
        self.assertEqual(profile.llm_provider, "openai")

    def test_user_can_change_llm_provider_to_anthropic(self):
        User = get_user_model()
        user = User.objects.create_user(username="u2", email="u2@example.com", password="pw")

        self.client.force_login(user)
        profile = user.profile

        response = self.client.post(
            reverse("accounts:user_config_user"),
            data={
                "default_language": profile.default_language,
                "default_language_variant": profile.default_language_variant,
                "language_switching_permitted": "on",
                "persist_language_switch_for_session": "on",
                "summary_rollup_trigger_message_count": str(profile.summary_rollup_trigger_message_count),
                "llm_provider": "anthropic",
            },
        )

        self.assertEqual(response.status_code, 302)
        profile.refresh_from_db()
        self.assertEqual(profile.llm_provider, "anthropic")

    def test_user_can_change_llm_provider_to_deepseek(self):
        User = get_user_model()
        user = User.objects.create_user(username="u5", email="u5@example.com", password="pw")

        self.client.force_login(user)
        profile = user.profile

        response = self.client.post(
            reverse("accounts:user_config_user"),
            data={
                "default_language": profile.default_language,
                "default_language_variant": profile.default_language_variant,
                "language_switching_permitted": "on",
                "persist_language_switch_for_session": "on",
                "summary_rollup_trigger_message_count": str(profile.summary_rollup_trigger_message_count),
                "llm_provider": "deepseek",
            },
        )

        self.assertEqual(response.status_code, 302)
        profile.refresh_from_db()
        self.assertEqual(profile.llm_provider, "deepseek")

    def test_config_menu_updates_provider_and_model_versions(self):
        User = get_user_model()
        user = User.objects.create_user(username="u3", email="u3@example.com", password="pw")
        user.profile.openai_model_default = "gpt-5.1"
        user.profile.anthropic_model_default = "claude-sonnet-4-5-20250929"
        user.profile.save(update_fields=["openai_model_default", "anthropic_model_default"])

        self.client.force_login(user)
        response = self.client.post(
            reverse("accounts:config_menu"),
            data={
                "llm_provider": "anthropic",
                "anthropic_model_default": "claude-opus-4-5-20251101",
            },
        )

        self.assertEqual(response.status_code, 302)
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.llm_provider, "anthropic")
        self.assertEqual(user.profile.openai_model_default, "gpt-5.1")
        self.assertEqual(user.profile.anthropic_model_default, "claude-opus-4-5-20251101")

    def test_config_menu_updates_openai_model_only_when_openai_selected(self):
        User = get_user_model()
        user = User.objects.create_user(username="u4", email="u4@example.com", password="pw")
        user.profile.openai_model_default = "gpt-5.1"
        user.profile.anthropic_model_default = "claude-sonnet-4-5-20250929"
        user.profile.save(update_fields=["openai_model_default", "anthropic_model_default"])

        self.client.force_login(user)
        response = self.client.post(
            reverse("accounts:config_menu"),
            data={
                "llm_provider": "openai",
                "openai_model_default": "gpt-4.1-mini",
            },
        )

        self.assertEqual(response.status_code, 302)
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.llm_provider, "openai")
        self.assertEqual(user.profile.openai_model_default, "gpt-4.1-mini")
        self.assertEqual(user.profile.anthropic_model_default, "claude-sonnet-4-5-20250929")

    def test_config_menu_updates_deepseek_model_only_when_deepseek_selected(self):
        User = get_user_model()
        user = User.objects.create_user(username="u6", email="u6@example.com", password="pw")
        user.profile.openai_model_default = "gpt-5.1"
        user.profile.deepseek_model_default = "deepseek-chat"
        user.profile.save(update_fields=["openai_model_default", "deepseek_model_default"])

        self.client.force_login(user)
        response = self.client.post(
            reverse("accounts:config_menu"),
            data={
                "llm_provider": "deepseek",
                "deepseek_model_default": "deepseek-reasoner",
            },
        )

        self.assertEqual(response.status_code, 302)
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.llm_provider, "deepseek")
        self.assertEqual(user.profile.openai_model_default, "gpt-5.1")
        self.assertEqual(user.profile.deepseek_model_default, "deepseek-reasoner")
