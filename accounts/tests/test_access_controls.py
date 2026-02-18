import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from chats.models import ChatMessage, ChatWorkspace
from projects.models import Project


@override_settings(MEDIA_ROOT=tempfile.gettempdir())
class AccessControlTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username="owner_ac", email="owner_ac@example.com", password="pw")
        self.other = User.objects.create_user(username="other_ac", email="other_ac@example.com", password="pw")

        self.project = Project.objects.create(
            name="AC Project",
            owner=self.owner,
            purpose="Access control test",
            kind=Project.Kind.STANDARD,
            primary_type=Project.PrimaryType.DELIVERY,
            mode=Project.Mode.PLAN,
            status=Project.Status.ACTIVE,
        )
        self.chat = ChatWorkspace.objects.create(
            project=self.project,
            title="AC Chat",
            created_by=self.owner,
            status=ChatWorkspace.Status.ACTIVE,
        )

    def test_non_member_cannot_open_chat_detail(self):
        self.client.force_login(self.other)
        resp = self.client.get(reverse("accounts:chat_detail", args=[self.chat.id]))
        self.assertEqual(resp.status_code, 404)

    def test_non_member_cannot_post_chat_message(self):
        self.client.force_login(self.other)
        resp = self.client.post(
            reverse("accounts:chat_message_create"),
            {
                "chat_id": str(self.chat.id),
                "content": "hello",
            },
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(ChatMessage.objects.filter(chat=self.chat).count(), 0)

    def test_non_member_cannot_upload_attachment_to_chat(self):
        self.client.force_login(self.other)
        upload = SimpleUploadedFile("a.txt", b"hello", content_type="text/plain")
        resp = self.client.post(
            reverse("accounts:chat_attachment_upload", args=[self.chat.id]),
            {"file": upload, "source": "filepicker"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_non_member_cannot_open_project_home(self):
        self.client.force_login(self.other)
        resp = self.client.get(reverse("accounts:project_home", args=[self.project.id]))
        self.assertEqual(resp.status_code, 404)
