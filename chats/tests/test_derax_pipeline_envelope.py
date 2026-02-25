from django.contrib.auth import get_user_model
from django.test import TestCase

from chats.services.contracts.pipeline import ContractContext, build_system_blocks
from projects.models import Project, WorkItem


class DeraxPipelineEnvelopeTests(TestCase):
    def test_derax_envelope_included_with_phase_contract(self):
        User = get_user_model()
        user = User.objects.create_user(username="derax_pipe_u", email="derax_pipe_u@example.com", password="pw")
        project = Project.objects.create(
            name="Derax Pipeline Project",
            owner=user,
            workflow_mode=Project.WorkflowMode.DERAX_WORK,
        )
        work_item = WorkItem.create_minimal(project=project, active_phase=WorkItem.PHASE_DEFINE)

        blocks, trace = build_system_blocks(
            ContractContext(
                user=user,
                project=project,
                work_item=work_item,
                active_phase=WorkItem.PHASE_DEFINE,
                is_derax=True,
                include_envelope=False,
            )
        )
        ordered = [b["key"] for b in trace.get("ordered_blocks") or []]
        self.assertIn("derax.envelope", ordered)
        self.assertIn("phase.contract", ordered)
        self.assertTrue(any("DERAX JSON ENVELOPE" in b for b in blocks))
