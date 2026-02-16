from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from chats.models import ChatMessage, ChatRollupEvent, ChatWorkspace
from chats.services.pinning import (
    build_history_messages,
    build_pinned_system_block,
    rollup_segment,
    should_auto_rollup,
    undo_last_rollup,
)
from projects.models import Project


class PinningRollupTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="pin_u", email="pin_u@example.com", password="pw")
        self.project = Project.objects.create(name="Pinning Project", owner=self.user)
        self.chat = ChatWorkspace.objects.create(
            project=self.project,
            title="Pinning chat",
            created_by=self.user,
        )

    def _mk_turn(self, idx: int):
        u = ChatMessage.objects.create(
            chat=self.chat,
            role=ChatMessage.Role.USER,
            raw_text=f"user-{idx}",
        )
        a = ChatMessage.objects.create(
            chat=self.chat,
            role=ChatMessage.Role.ASSISTANT,
            raw_text=f"assistant-{idx}",
            answer_text=f"assistant-{idx}",
        )
        return u, a

    def test_auto_rollup_triggers_at_20_messages(self):
        for i in range(10):
            self._mk_turn(i)
        self.assertTrue(should_auto_rollup(self.chat))

    def test_auto_rollup_uses_user_threshold(self):
        profile = self.user.profile
        profile.summary_rollup_trigger_message_count = 6
        profile.save(update_fields=["summary_rollup_trigger_message_count"])

        for i in range(2):
            self._mk_turn(i)
        self.assertFalse(should_auto_rollup(self.chat, user=self.user))

        self._mk_turn(3)
        self.assertTrue(should_auto_rollup(self.chat, user=self.user))

    def test_manual_pin_advances_cursor_immediately(self):
        u1, a1 = self._mk_turn(1)
        u2, a2 = self._mk_turn(2)
        u2.importance = ChatMessage.Importance.PINNED
        u2.save(update_fields=["importance"])

        with patch("chats.services.pinning.generate_text", return_value='{"summary":"s","conclusion":"c"}'):
            rollup_segment(self.chat, upto_message_id=u2.id, user=self.user)

        self.chat.refresh_from_db()
        self.assertEqual(self.chat.pinned_cursor_message_id, u2.id)
        self.assertEqual(self.chat.pinned_summary, "s")
        self.assertEqual(self.chat.pinned_conclusion, "c")
        self.assertTrue(ChatRollupEvent.objects.filter(chat=self.chat).exists())

    def test_ignore_messages_excluded_from_rollups(self):
        u1, a1 = self._mk_turn(1)
        u2, a2 = self._mk_turn(2)
        a1.raw_text = "IGNORE-ME"
        a1.answer_text = "IGNORE-ME"
        a1.importance = ChatMessage.Importance.IGNORE
        a1.save(update_fields=["raw_text", "answer_text", "importance"])

        def _fake_generate_text(*, system_blocks, messages, user=None, provider=None):
            payload = messages[0]["content"]
            self.assertNotIn("IGNORE-ME", payload)
            return '{"summary":"ok","conclusion":"ok"}'

        with patch("chats.services.pinning.generate_text", side_effect=_fake_generate_text):
            rollup_segment(self.chat, user=self.user)

        self.chat.refresh_from_db()
        self.assertEqual(self.chat.pinned_summary, "ok")

    def test_quick_mode_uses_only_previous_turn(self):
        self._mk_turn(1)
        self._mk_turn(2)
        self._mk_turn(3)

        history = build_history_messages(self.chat, answer_mode="quick")
        self.assertEqual(len(history), 2)
        self.assertIn("user-3", history[0]["content"][0]["text"])
        self.assertIn("assistant-3", history[1]["content"][0]["text"])

    def test_full_mode_uses_messages_since_cursor(self):
        _u1, a1 = self._mk_turn(1)
        self._mk_turn(2)
        self._mk_turn(3)
        self.chat.pinned_cursor_message_id = a1.id
        self.chat.save(update_fields=["pinned_cursor_message_id"])

        history = build_history_messages(self.chat, answer_mode="full")
        self.assertEqual(len(history), 4)
        self.assertIn("user-2", history[0]["content"][0]["text"])
        self.assertIn("assistant-3", history[-1]["content"][0]["text"])

    def test_context_builder_injects_pinned_summary(self):
        self.chat.pinned_summary = "- point one\n- point two"
        self.chat.pinned_conclusion = "Short conclusion."
        self.chat.save(update_fields=["pinned_summary", "pinned_conclusion"])

        block = build_pinned_system_block(self.chat)
        self.assertIn("Summary:", block)
        self.assertIn("Conclusion:", block)
        self.assertIn("point one", block)
        self.assertIn("Short conclusion.", block)

    def test_undo_last_rollup_restores_previous_state(self):
        self.chat.pinned_summary = "before-s"
        self.chat.pinned_conclusion = "before-c"
        self.chat.pinned_cursor_message_id = 1
        self.chat.save(update_fields=["pinned_summary", "pinned_conclusion", "pinned_cursor_message_id"])

        _u, a = self._mk_turn(1)
        with patch("chats.services.pinning.generate_text", return_value='{"summary":"after-s","conclusion":"after-c"}'):
            rollup_segment(self.chat, user=self.user)

        self.chat.refresh_from_db()
        self.assertEqual(self.chat.pinned_summary, "after-s")

        res = undo_last_rollup(self.chat, user=self.user)
        self.assertTrue(res.get("undone"))
        self.chat.refresh_from_db()
        self.assertEqual(self.chat.pinned_summary, "before-s")
        self.assertEqual(self.chat.pinned_conclusion, "before-c")
        self.assertEqual(self.chat.pinned_cursor_message_id, 1)
        ev = ChatRollupEvent.objects.filter(chat=self.chat).order_by("-id").first()
        self.assertIsNotNone(ev)
        self.assertIsNotNone(ev.reverted_at)
