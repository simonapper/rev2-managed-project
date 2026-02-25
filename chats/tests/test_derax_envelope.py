from django.test import SimpleTestCase

from chats.services.derax.envelope import build_derax_system_blocks


class DeraxEnvelopeTests(SimpleTestCase):
    def test_build_derax_system_blocks_contains_json_only_rule(self):
        blocks = build_derax_system_blocks(base_system_blocks=["BASE"], phase="DEFINE")
        self.assertGreaterEqual(len(blocks), 3)
        self.assertIn("Return ONLY a single JSON object.", blocks[0])
        self.assertIn("No markdown. No commentary. No text outside JSON.", blocks[0])
        self.assertEqual(blocks[-1], "BASE")
