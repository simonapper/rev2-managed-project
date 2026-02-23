from django.contrib.auth import get_user_model
from django.test import TestCase

from projects.models import Project, WorkItem
from projects.services_codex_instruction import generate_codex_instruction


class CodexInstructionGeneratorTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="codex_instruction_owner",
            email="codex_instruction_owner@example.com",
            password="pw",
        )
        self.project = Project.objects.create(name="Codex Instruction Project", owner=self.owner)

    def test_generate_fails_without_locked_seed(self):
        work_item = WorkItem.create_minimal(project=self.project, active_phase=WorkItem.PHASE_REFINE)
        work_item.append_seed_revision("Draft seed", self.owner, "Initial")
        with self.assertRaises(ValueError):
            generate_codex_instruction(work_item)

    def test_generate_contains_required_headers(self):
        work_item = WorkItem.create_minimal(project=self.project, active_phase=WorkItem.PHASE_REFINE)
        work_item.append_seed_revision("Locked seed text", self.owner, "Initial")
        work_item.lock_seed(1)
        work_item.set_phase(WorkItem.PHASE_EXECUTE)

        md = generate_codex_instruction(work_item)
        work_item.refresh_from_db()

        self.assertIn("# Goal (locked seed)", md)
        self.assertIn("# Scope (in / out)", md)
        self.assertIn("# Files to change (placeholders acceptable if unknown)", md)
        self.assertIn("# Invariants (seed_log append-only, single PASS_LOCKED, etc.)", md)
        self.assertIn("# Step-by-step tasks (numbered)", md)
        self.assertIn("# Tests (how to verify)", md)
        self.assertIn("# Don’t-do list (prevent scope creep)", md)
        self.assertTrue(len(work_item.deliverables) >= 1)
        self.assertEqual(work_item.active_phase, WorkItem.PHASE_COMPLETE)
