from django.test import TestCase

from projects.services_text_normalise import normalise_sections


class NormaliseSectionsTests(TestCase):
    def test_normalise_sections(self):
        raw = "# Title\nLine1\n\n\nLine2\n# Next\nBody"
        out = normalise_sections(raw)
        self.assertIn("# Title\n\nLine1\n\nLine2\n\n# Next\n\nBody\n", out)
