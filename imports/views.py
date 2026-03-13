# -*- coding: utf-8 -*-
# imports/views.py

import json
import os
import tempfile

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.urls import reverse

from projects.models import Project
from accounts.models import User
from projects.services_project_membership import accessible_projects_qs

# from .chatgpt_export_parser import linearise_conversation, group_into_turns
from .services.chatgpt_importer import import_chatgpt_json

@login_required
def import_preview_detail(request, idx: int):
    import json

    with open(request.session["chatgpt_temp_file"], "r", encoding="utf-8") as f:
        data = json.load(f)

    conversations = (data.get("conversations") or data.get("chats")) if isinstance(data, dict) else data
    conv = conversations[idx]
    turns = conv.get("turns", [])

    return render(
        request,
        "accounts/import_preview_detail.html",
        {
            "conv": conv,
            "turns": turns,
            "conv_id": idx,
        },
    )

@login_required
def preview_import(request):
    users = User.objects.filter(is_active=True).order_by("username")
    projects = accessible_projects_qs(request.user).select_related("owner").order_by("name")

    preview_turns = []
    preview_title = ""
    uploaded_file_name = None
    conversations = []
    items = []

    selected_project_id = str(request.session.get("chatgpt_project_id") or "")
    selected_user_id = str(request.session.get("chatgpt_user_id") or "")

    if request.method == "POST":
        uploaded_file = request.FILES.get("chatgpt_file")
        if not uploaded_file:
            messages.error(request, "Please choose a JSON file.")
            return redirect("imports:preview_import")

        uploaded_file_name = getattr(uploaded_file, "name", "upload.json")

        project_id = request.POST.get("project_id") or selected_project_id
        user_id = request.POST.get("user") or selected_user_id

        if not project_id:
            messages.error(request, "Please select a project.")
            return redirect("imports:preview_import")

        if not user_id:
            messages.error(request, "Please select a user.")
            return redirect("imports:preview_import")

        try:
            pid = int(project_id)
        except ValueError:
            messages.error(request, "Invalid project selection.")
            return redirect("imports:preview_import")

        project = projects.filter(id=pid).first()
        if not project:
            messages.error(request, "Project not found or not accessible.")
            return redirect("imports:preview_import")

        try:
            uid = int(user_id)
        except ValueError:
            messages.error(request, "Invalid user selection.")
            return redirect("imports:preview_import")

        selected_user = users.filter(id=uid).first()
        if not selected_user:
            messages.error(request, "User not found.")
            return redirect("imports:preview_import")

        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"{request.user.id}_chatgpt_import.json")

        try:
            with open(temp_path, "wb") as f:
                for chunk in uploaded_file.chunks():
                    f.write(chunk)
        except Exception as e:
            messages.error(request, f"Could not save upload: {e}")
            return redirect("imports:preview_import")

        request.session["chatgpt_temp_file"] = temp_path
        request.session["chatgpt_project_id"] = project.id
        request.session["chatgpt_user_id"] = selected_user.id
        request.session["chatgpt_import_offset"] = 0
        request.session.modified = True

        selected_project_id = str(project.id)
        selected_user_id = str(selected_user.id)

        try:
            with open(temp_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messages.error(request, f"Invalid JSON file: {e}")
            return redirect("imports:preview_import")

        conversations = (data.get("conversations") or data.get("chats")) if isinstance(data, dict) else data
        if not isinstance(conversations, list) or not conversations:
            messages.error(request, "No conversations found in the export.")
            return redirect("imports:preview_import")

        # build items for the list
        items = []
        for idx, c in enumerate(conversations):
            items.append({
                "idx": idx,
                "title": c.get("title") or "Untitled Conversation",
                "updated": c.get("update_time_iso") or c.get("update_time") or "",
            })

        # preview first chat that has turns
        preview_turns = []
        preview_title = ""
        for conv in conversations[:5]:
            turns = conv.get("turns") or []
            if turns:
                preview_turns = turns
                preview_title = conv.get("title") or "Untitled Conversation"
                break

        if not preview_turns:
            messages.error(request, "No turns found in the first 5 conversations.")

    return render(
        request,
        "imports/preview_import.html",
        {
            "users": users,
            "projects": projects,
            "items": items,
            "preview_turns": preview_turns,
            "preview_title": preview_title,
            "uploaded_file_name": uploaded_file_name,
            "selected_project_id": selected_project_id,
            "selected_user_id": selected_user_id,
        },
    )




@login_required
def import_chatgpt_action(request):
    """
    Import the ChatGPT JSON file saved during preview into the selected project.
    Imports ONE conversation per click (session offset).
    """
    if request.method != "POST":
        return redirect("imports:preview_import")

    temp_path = request.session.get("chatgpt_temp_file")
    project_id = request.session.get("chatgpt_project_id")
    user_id = request.session.get("chatgpt_user_id")

    if not temp_path or not os.path.exists(temp_path):
        messages.error(request, "No file available to import. Please upload and preview first.")
        return redirect("imports:preview_import")

    projects = accessible_projects_qs(request.user)
    project = projects.filter(id=project_id).first()
    if not project:
        messages.error(request, "Project not found or not accessible.")
        return redirect("imports:preview_import")

    selected_user = User.objects.filter(id=user_id, is_active=True).first()
    if not selected_user:
        messages.error(request, "User not found.")
        return redirect("imports:preview_import")

    # Load JSON
    try:
        with open(temp_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        messages.error(request, f"Invalid JSON file: {e}")
        return redirect("imports:preview_import")

#   conversations_all = data.get("conversations") if isinstance(data, dict) else data
    conversations_all = (data.get("conversations") or data.get("chats")) if isinstance(data, dict) else data

    if not isinstance(conversations_all, list):
        messages.error(request, "Export JSON format not recognised.")
        return redirect("imports:preview_import")

    # # Import one conversation per click (offset stored in session)
    # start = int(request.session.get("chatgpt_import_offset", 0))
    # conversations = conversations_all[start:start + 1]

    # if not conversations:
    #     messages.info(request, "All conversations from this file have been imported.")
    #     return redirect("imports:preview_import")

    # request.session["chatgpt_import_offset"] = start + 1
    # request.session.modified = True

    # Import ALL conversations in one click
    conversations = conversations_all

    if not conversations:
        messages.error(request, "No conversations found to import.")
        return redirect("imports:preview_import")

    # Optional: clear any old incremental state
    request.session.pop("chatgpt_import_offset", None)
    request.session.modified = True

    # Import into DB
    try:
        imported_workspaces = import_chatgpt_json(conversations, project, selected_user)
    except Exception as e:
        messages.error(request, f"Import failed: {e}")
        return redirect("imports:preview_import")

    # messages.success(
    #     request,
    #     f"Imported {len(imported_workspaces)} conversation (#{start + 1}) into project '{project.name}'.",
    # )
    messages.success(
        request,
        f"Imported {len(imported_workspaces)} conversations into project '{project.name}'.",
    )
    return redirect(reverse("accounts:dashboard"))
