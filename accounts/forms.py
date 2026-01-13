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
    cognitive_avatar = forms.ModelChoiceField(
        queryset=Avatar.objects.filter(category=Avatar.Category.COGNITIVE, is_active=True).order_by("name"),
        required=True,
        widget=_SELECT_SM,
    )
    interaction_avatar = forms.ModelChoiceField(
        queryset=Avatar.objects.filter(category=Avatar.Category.INTERACTION, is_active=True).order_by("name"),
        required=True,
        widget=_SELECT_SM,
    )
    presentation_avatar = forms.ModelChoiceField(
        queryset=Avatar.objects.filter(category=Avatar.Category.PRESENTATION, is_active=True).order_by("name"),
        required=True,
        widget=_SELECT_SM,
    )
    epistemic_avatar = forms.ModelChoiceField(
        queryset=Avatar.objects.filter(category=Avatar.Category.EPISTEMIC, is_active=True).order_by("name"),
        required=True,
        widget=_SELECT_SM,
    )
    performance_avatar = forms.ModelChoiceField(
        queryset=Avatar.objects.filter(category=Avatar.Category.PERFORMANCE, is_active=True).order_by("name"),
        required=True,
        widget=_SELECT_SM,
    )
    checkpointing_avatar = forms.ModelChoiceField(
        queryset=Avatar.objects.filter(category=Avatar.Category.CHECKPOINTING, is_active=True).order_by("name"),
        required=True,
        widget=_SELECT_SM,
    )

    class Meta:
        model = UserProfile
        fields = (
            "default_language",
            "default_language_variant",
            "language_switching_permitted",
            "persist_language_switch_for_session",
            "cognitive_avatar",
            "interaction_avatar",
            "presentation_avatar",
            "epistemic_avatar",
            "performance_avatar",
            "checkpointing_avatar",
        )
        widgets = {
            "default_language": _TEXT_SM,
            "default_language_variant": _TEXT_SM,  # <-- changed to text box
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
            "active_l4_config": forms.Select(
                attrs={"class": "form-select form-select-sm"}
            ),
        }
