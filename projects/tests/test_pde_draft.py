from django.test import SimpleTestCase

from projects.services.pde import draft_pde_from_seed


class PDEDraftFromSeedTests(SimpleTestCase):
    def test_draft_accepts_fenced_json_output(self):
        def _fake_generate_panes(_input_text, image_parts=None, system_blocks=None):
            return {
                "output": """```json
{
  "hypotheses": {
    "fields": {
      "canonical.summary": "Test summary",
      "identity.project_type": "RESEARCH"
    }
  }
}
```"""
            }

        res = draft_pde_from_seed(
            generate_panes_func=_fake_generate_panes,
            seed_text="seed",
        )

        self.assertTrue(res.get("ok"))
        fields = ((res.get("draft") or {}).get("hypotheses") or {}).get("fields") or {}
        self.assertEqual(fields.get("canonical.summary"), "Test summary")
        self.assertEqual(fields.get("identity.project_type"), "RESEARCH")

    def test_draft_accepts_json_when_model_puts_it_in_answer_pane(self):
        def _fake_generate_panes(_input_text, image_parts=None, system_blocks=None):
            return {
                "output": "",
                "answer": """```json
{
  "hypotheses": {
    "fields": {
      "canonical.summary": "Answer pane summary"
    }
  }
}
```""",
            }

        res = draft_pde_from_seed(
            generate_panes_func=_fake_generate_panes,
            seed_text="seed",
        )

        self.assertTrue(res.get("ok"))
        fields = ((res.get("draft") or {}).get("hypotheses") or {}).get("fields") or {}
        self.assertEqual(fields.get("canonical.summary"), "Answer pane summary")
