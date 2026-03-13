# -*- coding: utf-8 -*-
# imports/management/commands/import_chatgpt_bulk.py
import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction

from projects.models import Project
from projects.services import accessible_projects_qs
from imports.services.chatgpt_importer import import_chatgpt_json

User = get_user_model()


class Command(BaseCommand):
    help = "Bulk import ChatGPT export JSON into a Workbench project"

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="Path to ChatGPT export JSON file")
        parser.add_argument("--project", required=True, type=int, help="Project ID to import into")
        parser.add_argument("--user", required=True, help="Username to assign as created_by")
        parser.add_argument("--limit", type=int, default=0, help="Limit conversations (0 = all)")
        parser.add_argument("--offset", type=int, default=0, help="Skip first N conversations")
        parser.add_argument("--dry-run", action="store_true", help="Parse only, do not write to DB")

    def handle(self, *args, **opts):
        file_path = Path(opts["file"])
        project_id = opts["project"]
        username = opts["user"]
        limit = int(opts["limit"] or 0)
        offset = int(opts["offset"] or 0)
        dry_run = bool(opts["dry_run"])

        if not file_path.exists():
            self.stderr.write(self.style.ERROR(f"File not found: {file_path}"))
            return

        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Invalid JSON: {e}"))
            return

        conversations_all = data.get("conversations") if isinstance(data, dict) else data
        if not isinstance(conversations_all, list):
            self.stderr.write(self.style.ERROR("Export JSON format not recognised (expected list or dict with 'conversations')."))
            return

        conversations = conversations_all[offset:]
        if limit > 0:
            conversations = conversations[:limit]

        try:
            project = Project.objects.get(id=project_id)
        except Project.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Project {project_id} not found"))
            return

        try:
            user = User.objects.get(username=username, is_active=True)
        except User.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"User '{username}' not found/active"))
            return

        self.stdout.write(f"Conversations in file: {len(conversations_all)}")
        self.stdout.write(f"Importing: {len(conversations)} (offset={offset}, limit={limit or 'all'})")
        self.stdout.write(f"Target project: {project.id} {project.name}")
        self.stdout.write(f"Created_by user: {user.username}")
        self.stdout.write(f"Dry run: {dry_run}")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN: not writing to DB"))
            return

        with transaction.atomic():
            workspaces = import_chatgpt_json(conversations, project, user)

        self.stdout.write(self.style.SUCCESS(f"Imported {len(workspaces)} conversations into '{project.name}'"))
