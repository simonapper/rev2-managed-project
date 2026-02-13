from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from chats.models import ChatMessage
from config.models import SystemConfigPointers
from projects.models import Project, ProjectCKO, ProjectPlanningPurpose
from projects.services_review_chat import get_or_create_review_chat


class ReviewChatSeedTests(TestCase):
    def test_review_seed_mentions_cko(self):
        User = get_user_model()
        owner = User.objects.create_user(username="owner", password="pw")

        SystemConfigPointers.objects.create(pk=1)

        project = Project.objects.create(
            name="P1",
            owner=owner,
            kind=Project.Kind.STANDARD,
        )
        cko = ProjectCKO.objects.create(
            project=project,
            version=1,
            status=ProjectCKO.Status.ACCEPTED,
            created_by=owner,
        )
        project.defined_cko = cko
        project.save(update_fields=["defined_cko"])
        ProjectPlanningPurpose.objects.create(project=project)

        chat = get_or_create_review_chat(
            project=project,
            user=owner,
            marker="INTENT",
            seed_text="Seed.",
            session_overrides={},
        )
        msg = ChatMessage.objects.filter(chat=chat, role=ChatMessage.Role.USER).order_by("id").first()
        self.assertIsNotNone(msg)
        self.assertIn("CKO (Canonical Knowledge Object)", msg.raw_text or "")
        self.assertIn("blank line", msg.raw_text or "")
