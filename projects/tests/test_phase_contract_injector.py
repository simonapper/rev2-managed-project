from django.contrib.auth import get_user_model
from django.test import TestCase

from chats.services.contracts.phase_resolver import _render_work_item_phase_contract
from projects.models import Project, WorkItem
from projects.phase_contracts import PHASE_CONTRACTS


class PhaseContractInjectorTests(TestCase):
    def test_refine_phase_contract_appears_in_prompt(self):
        User = get_user_model()
        owner = User.objects.create_user(
            username="phase_inject_owner",
            email="phase_inject_owner@example.com",
            password="pw",
        )
        project = Project.objects.create(name="Phase Inject Project", owner=owner)
        work_item = WorkItem.create_minimal(project=project, active_phase=WorkItem.PHASE_REFINE)
        work_item.append_seed_revision("Seed text alpha", owner, "Initial")

        prompt, _key = _render_work_item_phase_contract(work_item, user_text="Hello")

        self.assertIn("PHASE CONTRACT", prompt)
        self.assertIn("Active phase: REFINE", prompt)
        self.assertIn(
            f"Phase goal: {PHASE_CONTRACTS['REFINE']['phase_goal']}",
            prompt,
        )

    def test_illegal_phase_request_adds_gate_warning(self):
        User = get_user_model()
        owner = User.objects.create_user(
            username="phase_warn_owner",
            email="phase_warn_owner@example.com",
            password="pw",
        )
        project = Project.objects.create(name="Phase Warn Project", owner=owner)
        work_item = WorkItem.create_minimal(project=project, active_phase=WorkItem.PHASE_REFINE)
        work_item.append_seed_revision("Seed text beta", owner, "Initial")

        prompt, _key = _render_work_item_phase_contract(work_item, user_text="Please move to EXECUTE now.")

        self.assertIn("Phase gate warning:", prompt)
        self.assertIn("EXECUTE", prompt)
