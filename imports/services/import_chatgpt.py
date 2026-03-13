# -*- coding: utf-8 -*-
# imports/services/import_chatgpt.py
# Purpose: Import ChatGPT export JSON into Workbench (thin wrapper over imports/services)

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Dict

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction

from projects.models import Project
from imports.services.chatgpt_importer import import_chatgpt_json

User = get_user_model()


class Command(BaseCommand):
    help = "Import ChatGPT export JSON into Workbench (uses imports.services.chatgpt_importer)"

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="Path to ChatGPT export JSON file")
        parser.add_argument("--project", required=True, type=int, help="Workbench Project ID")
        parser.add_argument(
            "--user",
            type=str,
            required=False,
            help="Username to assign as created_by (defaults to first superuser)",
        )
        parser.add_argument(
            "--conversation",
            type=str,
            default="all",
            help="Conversation index (single number), comma-separated list, or 'all' (default: all)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate/read/preview counts without writing to DB",
        )

    def handle(self, *args, **options):
        file_path = Path(options["file"])
        project_id = options["project"]
        username = options.get("user")
        conv_arg = (options.get("conversation") or "all").strip()
        dry_run = bool(options.get("dry_run"))

        # 1) Validate file exists
        if not file_path.exists():
            self.stderr.write(self.style.ERROR(f"File not found: {file_path}"))
            return

        # 2) Validate JSON
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            self.stderr.write(self.style.ERROR(f"Invalid JSON: {e}"))
            return

        if not isinstance(data, list):
            self.stderr.write(self.style.ERROR("Export JSON must be a list of conversations"))
            return

        # 3) Validate project
        try:
            project = Project.objects.get(id=project_id)
        except Project.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Project ID {project_id} not found"))
            return

        # 4) Resolve user
        if username:
            try:
                creator = User.objects.get(username=username)
            except User.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"User '{username}' not found"))
                return
        else:
            creator = User.objects.filter(is_superuser=True).first()
            if not creator:
                self.stderr.write(self.style.ERROR("No superuser found. Please specify --user"))
                return

        # 5) Select conversations
        selected: List[Dict[str, Any]]
        if conv_arg.lower() == "all":
            selected = data
            indices = list(range(len(data)))
        else:
            try:
                indices = [int(x.strip()) for x in conv_arg.split(",") if x.strip()]
            except ValueError:
                self.stderr.write(self.style.ERROR(f"Invalid --conversation argument: {conv_arg}"))
                return

            selected = []
            for i in indices:
                if 0 <= i < len(data):
                    selected.append(data[i])
                else:
                    self.stderr.write(self.style.WARNING(f"Conversation index {i} out of range, skipping"))

        # 6) Dry run summary
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no DB writes\n"))
            self.stdout.write(f"File: {file_path}")
            self.stdout.write(f"Project: {project_id}")
            self.stdout.write(f"User: {creator.username}")
            self.stdout.write(f"Total conversations in file: {len(data)}")
            self.stdout.write(f"Selected conversations: {len(selected)}")
            if indices:
                self.stdout.write(f"Selected indices: {indices[:50]}" + (" ..." if len(indices) > 50 else ""))
            return

        # 7) Import
        with transaction.atomic():
            workspaces = import_chatgpt_json(
                data=selected,
                project=project,
                user=creator,
            )

        self.stdout.write(
            self.style.SUCCESS(f"Imported {len(workspaces)} conversations into project {project_id}.")
        )
