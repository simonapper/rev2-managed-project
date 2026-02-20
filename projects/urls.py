# -*- coding: utf-8 -*-
# projects/urls.py
# Purpose:
# Project-scoped UI routes

from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from projects import views
from projects import views_project
from projects import views_pde_ui
from projects import views_ppde_ui
from projects import views_review
from projects import views_wko
from projects import views_execution
from projects.views_pde import pde_create_project_cko, pde_field_lock, pde_field_verify, pde_home, pde_commit
from projects import views_cko

app_name = "projects"

urlpatterns = [
    path("<int:project_id>/rename/", views.rename_project, name="rename_project"),
    path("<int:project_id>/preferences/", views.project_preferences, name="project_preferences"),
    path("pde/field/verify/", pde_field_verify, name="pde_field_verify"),
    path("pde/field/lock/", pde_field_lock, name="pde_field_lock"),
    path("pde/create_project_cko/", pde_create_project_cko, name="pde_create_project_cko"),
    path("<int:project_id>/pde/", pde_home, name="pde_home"),
    # path("<int:project_id>/mark_sandbox/", views.project_mark_sandbox, name="project_mark_sandbox"),
    path("<int:project_id>/pde/commit/", pde_commit, name="pde_commit"),
    path("<int:project_id>/pde/ui/", views_pde_ui.pde_detail, name="pde_detail"),
    path("<int:project_id>/ppde/ui/", views_ppde_ui.ppde_detail, name="ppde_detail"),
    path("<int:project_id>/review/", views_review.project_review, name="project_review"),
    path("<int:project_id>/planning-mode/", views_project.set_planning_mode, name="set_planning_mode"),
    path("<int:project_id>/policy-docs/help/", views_project.policy_docs_help, name="policy_docs_help"),
    path("<int:project_id>/review/open-chat/", views_review.project_review_chat_open, name="project_review_chat_open"),
    path("<int:project_id>/review/seed-intent/", views_review.project_review_intent_seed, name="project_review_intent_seed"),
    path("<int:project_id>/review/seed-route/", views_review.project_review_route_seed_from_intent, name="project_review_route_seed_from_intent"),
    path("<int:project_id>/review/restore-route/", views_review.project_review_route_restore, name="project_review_route_restore"),
    path("<int:project_id>/review/print/intent/", views_review.project_review_print_intent, name="project_review_print_intent"),
    path("<int:project_id>/review/print/route/", views_review.project_review_print_route, name="project_review_print_route"),
    path("<int:project_id>/review/print/execute/", views_review.project_review_print_execute, name="project_review_print_execute"),
    path("<int:project_id>/review/open-stage-chat/", views_review.project_review_stage_chat_open, name="project_review_stage_chat_open"),
    path("<int:project_id>/review/update-anchor/", views_review.project_review_anchor_update, name="project_review_anchor_update"),
    path("<int:project_id>/review/anchor-status/", views_review.project_review_anchor_status, name="project_review_anchor_status"),
    path("<int:project_id>/review/execute-reseed/", views_review.project_review_execute_reseed, name="project_review_execute_reseed"),
    path("<int:project_id>/ppde/topic-chat/", views_ppde_ui.ppde_topic_chat_open, name="ppde_topic_chat_open"),
    path("<int:project_id>/pde/topic-chat/", views_pde_ui.pde_topic_chat_open, name="pde_topic_chat_open"),
    path("<int:project_id>/wko/preview/", views_wko.wko_preview, name="wko_preview"),
    path("<int:project_id>/execution/", views_execution.execution_board, name="execution_board"),
    path("<int:project_id>/home/", views_project.project_home, name="project_home"),
    path("<int:project_id>/cko/preview/", views_cko.cko_preview, name="cko_preview"),
    path("<int:project_id>/cko/print/", views_cko.cko_print, name="cko_print"),
    path("<int:project_id>/cko/<int:cko_id>/accept/", views_cko.cko_accept, name="cko_accept"),



]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
