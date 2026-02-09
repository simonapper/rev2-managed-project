# -*- coding: utf-8 -*-

from __future__ import annotations

import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from projects.models import Project, ProjectCKO, ProjectMembership
from projects.services_project_membership import is_project_committer


@override_settings(MEDIA_ROOT=tempfile.gettempdir())
class ProjectCoreTeamTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username="owner", email="o@example.com", password="pw")
        self.c1 = User.objects.create_user(username="c1", email="c1@example.com", password="pw")
        self.c2 = User.objects.create_user(username="c2", email="c2@example.com", password="pw")

    def test_project_create_assigns_contributors(self):
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("accounts:project_create"),
            {
                "name": "P1",
                "purpose": "Test project",
                "kind": "STANDARD",
                "primary_type": "DELIVERY",
                "mode": "PLAN",
                "contributors": [self.c1.id, self.c2.id],
            },
        )
        self.assertEqual(resp.status_code, 302)
        project = Project.objects.get(name="P1")
        self.assertTrue(is_project_committer(project, self.owner))
        self.assertFalse(is_project_committer(project, self.c1))
        self.assertTrue(
            ProjectMembership.objects.filter(
                project=project,
                user=self.c1,
                role=ProjectMembership.Role.CONTRIBUTOR,
                status=ProjectMembership.Status.ACTIVE,
            ).exists()
        )
        self.assertTrue(
            ProjectMembership.objects.filter(
                project=project,
                user=self.c2,
                role=ProjectMembership.Role.CONTRIBUTOR,
                status=ProjectMembership.Status.ACTIVE,
            ).exists()
        )

    def test_contributor_cannot_commit_or_accept(self):
        project = Project.objects.create(
            name="P2",
            owner=self.owner,
            purpose="Test",
            kind=Project.Kind.STANDARD,
        )
        ProjectMembership.objects.create(
            project=project,
            user=self.c1,
            role=ProjectMembership.Role.CONTRIBUTOR,
            scope_type=ProjectMembership.ScopeType.PROJECT,
            scope_ref="",
            status=ProjectMembership.Status.ACTIVE,
        )

        self.client.force_login(self.c1)
        resp = self.client.post(
            reverse("projects:pde_detail", args=[project.id]),
            {"action": "commit"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ProjectCKO.objects.filter(project=project).count(), 0)

        cko = ProjectCKO.objects.create(
            project=project,
            version=1,
            status=ProjectCKO.Status.DRAFT,
            created_by=self.owner,
        )
        resp = self.client.post(reverse("projects:cko_accept", args=[project.id, cko.id]))
        self.assertEqual(resp.status_code, 302)
        cko.refresh_from_db()
        self.assertEqual(cko.status, ProjectCKO.Status.DRAFT)

    def test_committer_can_update_committee(self):
        project = Project.objects.create(
            name="P3",
            owner=self.owner,
            purpose="Test",
            kind=Project.Kind.STANDARD,
        )
        ProjectMembership.objects.create(
            project=project,
            user=self.c1,
            role=ProjectMembership.Role.CONTRIBUTOR,
            scope_type=ProjectMembership.ScopeType.PROJECT,
            scope_ref="",
            status=ProjectMembership.Status.ACTIVE,
        )

        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("accounts:project_config_info", args=[project.id]),
            {
                "action": "committee_update",
                "committer_id": str(self.c1.id),
                "member_ids": [str(self.owner.id), str(self.c1.id)],
                f"member_role_{self.owner.id}": "CONTRIBUTOR",
                f"member_role_{self.c1.id}": "CONTRIBUTOR",
                "add_user_ids": [str(self.c2.id)],
            },
        )
        self.assertEqual(resp.status_code, 302)
        project.refresh_from_db()
        self.assertEqual(project.owner_id, self.c1.id)
        self.assertTrue(
            ProjectMembership.objects.filter(
                project=project,
                user=self.c2,
                role=ProjectMembership.Role.CONTRIBUTOR,
                status=ProjectMembership.Status.ACTIVE,
            ).exists()
        )
