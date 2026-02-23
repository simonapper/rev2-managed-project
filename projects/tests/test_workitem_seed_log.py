from django.contrib.auth import get_user_model
from django.test import TestCase

from projects.models import Project, WorkItem


class WorkItemSeedLogTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username="wi_owner", email="wi_owner@example.com", password="pw")
        self.editor = User.objects.create_user(username="wi_editor", email="wi_editor@example.com", password="pw")
        self.project = Project.objects.create(name="WorkItem Seed Project", owner=self.owner)
        self.work_item = WorkItem.create_minimal(project=self.project)

    def test_append_seed_revision_is_append_only_and_increments(self):
        rev1 = self.work_item.append_seed_revision("Seed A", self.owner, "Initial")
        rev2 = self.work_item.append_seed_revision("Seed B", self.editor, "Refine")

        self.work_item.refresh_from_db()
        self.assertEqual(rev1, 1)
        self.assertEqual(rev2, 2)
        self.assertEqual(self.work_item.active_seed_revision, 2)
        self.assertEqual(len(self.work_item.seed_log), 2)
        self.assertEqual(self.work_item.seed_log[0]["revision"], 1)
        self.assertEqual(self.work_item.seed_log[1]["revision"], 2)
        self.assertEqual(self.work_item.seed_log[0]["seed_text"], "Seed A")
        self.assertEqual(self.work_item.seed_log[1]["seed_text"], "Seed B")

    def test_lock_seed_enforces_single_pass_locked(self):
        self.work_item.append_seed_revision("Seed A", self.owner, "Initial")
        self.work_item.append_seed_revision("Seed B", self.editor, "Refine")

        self.work_item.lock_seed(1)
        self.work_item.refresh_from_db()
        statuses = [row.get("status") for row in self.work_item.seed_log]
        self.assertEqual(statuses, [WorkItem.SEED_STATUS_PASS_LOCKED, WorkItem.SEED_STATUS_PROPOSED])
        self.assertEqual(self.work_item.active_seed_revision, 1)

        self.work_item.lock_seed(2)
        self.work_item.refresh_from_db()
        statuses = [row.get("status") for row in self.work_item.seed_log]
        self.assertEqual(statuses, [WorkItem.SEED_STATUS_RETIRED, WorkItem.SEED_STATUS_PASS_LOCKED])
        self.assertEqual(self.work_item.active_seed_revision, 2)

    def test_rollback_to_appends_new_revision_without_deleting_history(self):
        self.work_item.append_seed_revision("Seed A", self.owner, "Initial")
        self.work_item.append_seed_revision("Seed B", self.editor, "Refine")

        new_revision = self.work_item.rollback_to(1)
        self.work_item.refresh_from_db()

        self.assertEqual(new_revision, 3)
        self.assertEqual(len(self.work_item.seed_log), 3)
        self.assertEqual(self.work_item.active_seed_revision, 3)
        rollback_entry = self.work_item.seed_log[-1]
        self.assertEqual(rollback_entry["event"], "ROLLBACK")
        self.assertEqual(rollback_entry["rollback_to_revision"], 1)
        self.assertEqual(rollback_entry["seed_text"], "Seed A")

    def test_invariant_revisions_must_increase_by_one(self):
        self.work_item.seed_log = [
            {"revision": 1, "status": WorkItem.SEED_STATUS_PROPOSED, "seed_text": "A"},
            {"revision": 3, "status": WorkItem.SEED_STATUS_PROPOSED, "seed_text": "B"},
        ]
        self.work_item.save(update_fields=["seed_log", "updated_at"])

        with self.assertRaises(ValueError):
            self.work_item.append_seed_revision("Seed C", self.owner, "Should fail")
