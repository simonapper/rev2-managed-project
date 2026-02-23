import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from chats.models import ChatWorkspace
from config.models import SystemConfigPointers
from projects.models import Project, WorkItem


@override_settings(MEDIA_ROOT=tempfile.gettempdir(), CONTRACT_PIPELINE_ENABLED=True, DEBUG=True)
class MainTurnContractPipelineTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="ctp_owner", email="ctp_owner@example.com", password="pw")
        self.project = Project.objects.create(
            name="CTP Main Turn Project",
            owner=self.user,
            boundary_profile_json={
                "jurisdiction": "UK",
                "topic_tags": ["UK_TAX"],
                "authority_set": {"allow_internal_docs": False},
            },
        )
        self.chat = ChatWorkspace.objects.create(
            project=self.project,
            title="Main turn chat",
            created_by=self.user,
            status=ChatWorkspace.Status.ACTIVE,
        )
        self.work_item = WorkItem.create_minimal(
            project=self.project,
            active_phase=WorkItem.PHASE_REFINE,
            title="Pipeline phase item",
        )
        self.work_item.append_seed_revision("Seed text", created_by=self.user, reason="seed")
        self.work_item.lock_seed(1)
        SystemConfigPointers.objects.create(id=1, updated_by=self.user)
        self.client.force_login(self.user)

    def test_main_turn_pipeline_system_blocks_shape(self):
        captured = {}

        def _fake_generate_panes(*args, **kwargs):
            captured["system_blocks"] = list(kwargs.get("system_blocks") or [])
            return {
                "answer": (
                    "Scope: IN-SCOPE\n"
                    "Assumptions: jurisdiction UK.\n"
                    "Source basis: general_knowledge\n"
                    "Confidence: medium\n\n"
                    "# Seed summary\n"
                    "Summary.\n\n"
                    "# Inputs\n"
                    "Inputs.\n\n"
                    "# Expected outputs\n"
                    "Outputs.\n"
                ),
                "key_info": "",
                "visuals": "",
                "reasoning": "",
                "output": "",
            }

        with patch("accounts.views.generate_panes", side_effect=_fake_generate_panes):
            response = self.client.post(
                reverse("accounts:chat_message_create"),
                {"chat_id": str(self.chat.id), "content": "Please proceed."},
            )

        self.assertEqual(response.status_code, 302)
        system_blocks = list(captured.get("system_blocks") or [])
        self.assertTrue(system_blocks)
        self.assertTrue(system_blocks[0].startswith("Return JSON with keys:"))
        self.assertEqual(sum(1 for b in system_blocks if "BOUNDARY CONTRACT" in b.upper()), 1)
        self.assertEqual(sum(1 for b in system_blocks if "PHASE CONTRACT" in b.upper()), 1)
