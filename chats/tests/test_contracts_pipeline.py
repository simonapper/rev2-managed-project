# -*- coding: utf-8 -*-

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from chats.models import ChatWorkspace
from chats.services.contracts.boundary_resolver import resolve_boundary_contract
from chats.services.contracts.pipeline import ContractContext, build_system_blocks
from chats.services.llm import generate_panes
from projects.models import PhaseContract, Project, WorkItem


class ContractsPipelineTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="contract_u", email="contract_u@example.com", password="pw")
        self.project = Project.objects.create(
            name="Contracts Project",
            owner=self.user,
            boundary_profile_json={
                "jurisdiction": "UK",
                "topic_tags": ["UK_TAX"],
                "authority_set": {"allow_internal_docs": True},
            },
        )
        self.chat = ChatWorkspace.objects.create(project=self.project, title="Contracts Chat", created_by=self.user)
        self.work_item = WorkItem.create_minimal(
            project=self.project,
            active_phase=WorkItem.PHASE_REFINE,
            title="Pipeline item",
        )
        self.work_item.append_seed_revision("Initial seed", created_by=self.user, reason="test")
        self.work_item.lock_seed(1)
        self.ppde_contract = PhaseContract.objects.create(
            key="STRUCTURE_PROJECT_PIPELINE_TEST",
            title="Structure Project",
            version=1,
            is_active=True,
            purpose_text="Purpose",
            inputs_text="Inputs",
            outputs_text="Outputs",
            method_guidance_text="Method",
            acceptance_test_text="Acceptance",
            llm_review_prompt_text="Review",
        )

    def test_ordering_and_single_boundary_and_phase(self):
        ctx = ContractContext(
            user=self.user,
            chat=self.chat,
            project=self.project,
            work_item=self.work_item,
            user_text="Proceed.",
            is_ppde=True,
            ppde_phase_contract=self.ppde_contract,
            tier5_blocks=["BOUNDARY CONTRACT\nlegacy boundary block"],
            legacy_system_blocks=["BOUNDARY CONTRACT\nlegacy boundary block 2"],
        )
        _blocks, trace = build_system_blocks(ctx)

        ordered = trace["ordered_blocks"]
        self.assertTrue(ordered)
        self.assertEqual(ordered[0]["key"], "envelope.json_schema")

        boundary_blocks = [b for b in ordered if b["dedupe_group"] == "boundary"]
        self.assertEqual(len(boundary_blocks), 1)

        phase_blocks = [b for b in ordered if b["key"] == "phase.contract"]
        self.assertEqual(len(phase_blocks), 1)
        self.assertEqual(trace["effective_phase_contract"], "STRUCTURE_PROJECT_PIPELINE_TEST:v1")

    def test_boundary_precedence_prefers_work_item_overlay(self):
        self.work_item.boundary_profile_json = {
            "jurisdiction": "US",
            "topic_tags": ["US_TAX"],
            "required_labels": ["Scope", "Assumptions", "Source basis", "Confidence"],
        }
        self.work_item.save(update_fields=["boundary_profile_json", "updated_at"])

        ctx = ContractContext(project=self.project, chat=self.chat, work_item=self.work_item)
        out = resolve_boundary_contract(ctx)

        self.assertIsNotNone(out)
        self.assertEqual(out.effective_boundary.get("jurisdiction"), "US")
        self.assertEqual(out.effective_boundary.get("topic_tags"), ["US_TAX"])
        labels = out.effective_boundary.get("required_labels") or {}
        self.assertTrue(labels.get("scope_flag"))
        self.assertTrue(labels.get("assumptions"))
        self.assertTrue(labels.get("source_basis"))
        self.assertTrue(labels.get("confidence"))

    @override_settings(CONTRACT_PIPELINE_ENABLED=True)
    def test_generate_panes_pipeline_smoke(self):
        mock_client = Mock()
        mock_client.responses.create.return_value = SimpleNamespace(
            output=[SimpleNamespace(content=[SimpleNamespace(parsed={"answer": "ok", "key_info": "", "visuals": "", "reasoning": "", "output": ""})])]
        )

        ctx = ContractContext(
            user=self.user,
            chat=self.chat,
            project=self.project,
            work_item=self.work_item,
            user_text="Smoke test",
            is_review=True,
        )
        with patch("chats.services.llm._get_openai_client", return_value=mock_client):
            panes = generate_panes(
                user_text="Smoke test",
                user=self.user,
                provider="openai",
                contract_ctx=ctx,
            )

        self.assertEqual(panes["answer"], "ok")
        call_kwargs = mock_client.responses.create.call_args.kwargs
        input_msgs = call_kwargs["input"]
        system_msgs = [m for m in input_msgs if m.get("role") == "system"]
        self.assertTrue(system_msgs)
