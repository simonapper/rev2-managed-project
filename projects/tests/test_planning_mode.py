# -*- coding: utf-8 -*-

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from projects.models import Project, ProjectMembership


class PlanningModeTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username="pm_owner", email="pm_owner@example.com", password="pw")
        self.project = Project.objects.create(
            name="Planning Mode Project",
            owner=self.owner,
            purpose="Planning mode test",
            kind=Project.Kind.STANDARD,
        )

    def test_set_planning_mode_updates_membership_and_redirects(self):
        self.client.force_login(self.owner)
        next_url = reverse("accounts:project_config_info", args=[self.project.id]) + "?x=1"
        resp = self.client.post(
            reverse("projects:set_planning_mode", args=[self.project.id]),
            {"mode": "AUTO", "next": next_url},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], next_url)

        membership = ProjectMembership.objects.filter(
            project=self.project,
            user=self.owner,
            status=ProjectMembership.Status.ACTIVE,
            effective_to__isnull=True,
        ).first()
        self.assertIsNotNone(membership)
        self.assertEqual(membership.planning_mode, ProjectMembership.PlanningMode.AUTO)

    def test_project_config_info_renders_plan_url_for_mode(self):
        membership = ProjectMembership.objects.create(
            project=self.project,
            user=self.owner,
            role=ProjectMembership.Role.OWNER,
            scope_type=ProjectMembership.ScopeType.PROJECT,
            scope_ref="",
            status=ProjectMembership.Status.ACTIVE,
            planning_mode=ProjectMembership.PlanningMode.ASSISTED,
        )

        self.client.force_login(self.owner)
        resp_assisted = self.client.get(reverse("accounts:project_config_info", args=[self.project.id]))
        self.assertEqual(resp_assisted.status_code, 200)
        self.assertContains(resp_assisted, f'href="{reverse("projects:ppde_detail", args=[self.project.id])}">Plan</a>', html=False)

        membership.planning_mode = ProjectMembership.PlanningMode.AUTO
        membership.save(update_fields=["planning_mode", "updated_at"])

        resp_auto = self.client.get(reverse("accounts:project_config_info", args=[self.project.id]))
        self.assertEqual(resp_auto.status_code, 200)
        self.assertContains(resp_auto, f'href="{reverse("projects:project_review", args=[self.project.id])}">Plan</a>', html=False)
