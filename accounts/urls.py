# accounts/urls.py
from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    # One-time link target used in invite email
    path("set-password/<uidb64>/<token>/", views.set_password_from_invite, name="set_password"),
]
