# -*- coding: utf-8 -*-

from __future__ import annotations

import shutil
import os
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse

from chats.models import ChatMessage, ChatWorkspace
from chats.services_assets import save_generated_image_bytes
from projects.models import Project
from uploads.models import GeneratedImage


_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\x99c``\x00\x00"
    b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)


class GeneratedImageServiceTests(TestCase):
    def setUp(self):
        self._tmp_media = os.path.join("D:\\Workbench code", "_test_media_" + uuid.uuid4().hex)
        os.makedirs(self._tmp_media, exist_ok=True)
        self._media_override = override_settings(MEDIA_ROOT=self._tmp_media)
        self._media_override.enable()
        super().setUp()
        User = get_user_model()
        self.user = User.objects.create_user(username="img_u", email="img_u@example.com", password="pw")
        self.project = Project.objects.create(name="Img Project", owner=self.user)
        self.chat = ChatWorkspace.objects.create(project=self.project, title="Chat", created_by=self.user)
        self.msg = ChatMessage.objects.create(chat=self.chat, role=ChatMessage.Role.ASSISTANT, raw_text="x")

    def tearDown(self):
        self._media_override.disable()
        shutil.rmtree(self._tmp_media, ignore_errors=True)
        super().tearDown()

    def test_save_generated_image_bytes_creates_row_and_file(self):
        obj = save_generated_image_bytes(
            project=self.project,
            chat=self.chat,
            message=self.msg,
            prompt="draw icon",
            provider="openai",
            model="gpt-image-1",
            image_bytes=_PNG_1X1,
            mime_type="image/png",
        )
        self.assertTrue(GeneratedImage.objects.filter(id=obj.id).exists())
        self.assertTrue(bool(obj.image_file.name))
        self.assertTrue(obj.sha256)


class GeneratedImageViewTests(TestCase):
    def setUp(self):
        self._tmp_media = os.path.join("D:\\Workbench code", "_test_media_" + uuid.uuid4().hex)
        os.makedirs(self._tmp_media, exist_ok=True)
        self._media_override = override_settings(MEDIA_ROOT=self._tmp_media)
        self._media_override.enable()
        super().setUp()
        User = get_user_model()
        self.owner = User.objects.create_user(username="img_owner", email="img_owner@example.com", password="pw")
        self.other = User.objects.create_user(username="img_other", email="img_other@example.com", password="pw")
        self.project = Project.objects.create(name="Img View Project", owner=self.owner)
        self.chat = ChatWorkspace.objects.create(project=self.project, title="Chat", created_by=self.owner)
        self.msg = ChatMessage.objects.create(chat=self.chat, role=ChatMessage.Role.ASSISTANT, raw_text="x")
        self.asset = save_generated_image_bytes(
            project=self.project,
            chat=self.chat,
            message=self.msg,
            prompt="draw icon",
            provider="openai",
            model="gpt-image-1",
            image_bytes=_PNG_1X1,
            mime_type="image/png",
        )

    def tearDown(self):
        self._media_override.disable()
        shutil.rmtree(self._tmp_media, ignore_errors=True)
        super().tearDown()

    def test_authorised_user_can_view_and_download(self):
        self.client.force_login(self.owner)
        d = self.client.get(reverse("accounts:generated_image_detail", args=[self.asset.id]))
        self.assertEqual(d.status_code, 200)
        dl = self.client.get(reverse("accounts:generated_image_download", args=[self.asset.id]))
        self.assertEqual(dl.status_code, 200)

    def test_unauthorised_user_cannot_view_or_download(self):
        self.client.force_login(self.other)
        d = self.client.get(reverse("accounts:generated_image_detail", args=[self.asset.id]))
        self.assertEqual(d.status_code, 404)
        dl = self.client.get(reverse("accounts:generated_image_download", args=[self.asset.id]))
        self.assertEqual(dl.status_code, 404)
