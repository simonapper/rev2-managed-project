# -*- coding: utf-8 -*-
# accounts/forms.py

from __future__ import annotations

from django import forms

from accounts.models_avatars import Avatar, UserProfile
from projects.models import Project


_SELECT_SM = forms.Select(attrs={"class": "form-select form-select-sm"})
_TEXT_SM = forms.TextInput(attrs={"class": "form-control form-control-sm"})
_CHECK_SM = forms.CheckboxInput(attrs={"class": "form-check-input"})


class UserProfileDefaultsForm(forms.ModelForm):
    tone_avatar = forms.ModelChoiceField(
        queryset=Avatar.objects.filter(category=Avatar.Category.TONE, is_active=True).order_by("name"),
        required=False,
        widget=_SELECT_SM,
    )
    reasoning_avatar = forms.ModelChoiceField(
        queryset=Avatar.objects.filter(category=Avatar.Category.REASONING, is_active=True).order_by("name"),
        required=False,
        widget=_SELECT_SM,
    )
    approach_avatar = forms.ModelChoiceField(
        queryset=Avatar.objects.filter(category=Avatar.Category.APPROACH, is_active=True).order_by("name"),
        required=False,
        widget=_SELECT_SM,
    )
    control_avatar = forms.ModelChoiceField(
        queryset=Avatar.objects.filter(category=Avatar.Category.CONTROL, is_active=True).order_by("name"),
        required=False,
        widget=_SELECT_SM,
    )

    class Meta:
        model = UserProfile
        fields = (
            "default_language",
            "default_language_variant",
            "language_switching_permitted",
            "persist_language_switch_for_session",
            "tone_avatar",
            "reasoning_avatar",
            "approach_avatar",
            "control_avatar",
        )
        widgets = {
            "default_language": _TEXT_SM,
            "default_language_variant": _TEXT_SM,
            "language_switching_permitted": _CHECK_SM,
            "persist_language_switch_for_session": _CHECK_SM,
        }
        labels = {
            "default_language": "Preferred language",
            "default_language_variant": "Preferred language variant",
        }


class ProjectOperatingProfileForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ("active_l4_config",)
        widgets = {
            "active_l4_config": forms.Select(attrs={"class": "form-select form-select-sm"}),
        }
