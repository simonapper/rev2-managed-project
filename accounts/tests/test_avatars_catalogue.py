import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from projects.models import Project


@override_settings(MEDIA_ROOT=tempfile.gettempdir())
class AvatarCatalogueTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="avatar_admin",
            email="avatar_admin@example.com",
            password="pw",
            is_superuser=True,
            is_staff=True,
        )
        self.project = Project.objects.create(name="Avatar Catalogue Project", owner=self.user)
        self.client.force_login(self.user)

    def test_catalogue_page_renders_with_expected_columns(self):
        response = self.client.get(reverse("accounts:system_avatars_catalogue"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Avatar Catalogue")
        self.assertContains(response, "Axis")
        self.assertContains(response, "Preset name")
        self.assertContains(response, "Preview")

    def test_catalogue_includes_encouraging_tone(self):
        response = self.client.get(reverse("accounts:system_avatars_catalogue"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Encouraging")
