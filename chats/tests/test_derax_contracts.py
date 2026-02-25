from django.test import SimpleTestCase

from chats.services.derax.contracts import PHASE_MANIFEST, build_phase_contract_text
from chats.services.derax.phase_rules import required_paths_for_phase


class DeraxContractsTests(SimpleTestCase):
    def test_manifest_contains_define_and_explore(self):
        self.assertIn("DEFINE", PHASE_MANIFEST)
        self.assertIn("EXPLORE", PHASE_MANIFEST)

    def test_required_paths_from_manifest(self):
        paths = required_paths_for_phase("DEFINE")
        self.assertIn("intent.destination", paths)

    def test_contract_text_contains_target_paths(self):
        txt = build_phase_contract_text("DEFINE")
        self.assertIn("intent.destination", txt)
        self.assertIn("Return ONLY a single JSON object", txt)

    def test_refine_contract_text_includes_scope_control_rules(self):
        txt = build_phase_contract_text("REFINE")
        self.assertIn("Hierarchy rule", txt)
        self.assertIn("Realism rule", txt)
        self.assertIn("Compression rule", txt)

    def test_realism_and_precedence_rules_present(self):
        txt = build_phase_contract_text("DEFINE")
        self.assertIn("Precedence rule", txt)
        self.assertIn("Realism rule", txt)
        self.assertIn("Contention rule", txt)

    def test_execute_contract_renders(self):
        txt = build_phase_contract_text("EXECUTE")
        self.assertIn("Phase: EXECUTE", txt)
        self.assertIn("artefacts.proposed", txt)
        self.assertIn("Return ONLY a single JSON object", txt)

    def test_execute_manifest_present(self):
        self.assertIn("EXECUTE", PHASE_MANIFEST)
