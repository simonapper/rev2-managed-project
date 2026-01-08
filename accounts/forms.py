# -*- coding: utf-8 -*-
# accounts/forms.py

from __future__ import annotations

from django import forms

from accounts.models_avatars import Avatar, UserProfile


class UserProfileDefaultsForm(forms.ModelForm):
    cognitive_avatar = forms.ModelChoiceField(
        queryset=Avatar.objects.filter(
            category=Avatar.Category.COGNITIVE, is_active=True
        ).order_by("name"),
        required=True,
        empty_label=None,
    )
    interaction_avatar = forms.ModelChoiceField(
        queryset=Avatar.objects.filter(
            category=Avatar.Category.INTERACTION, is_active=True
        ).order_by("name"),
        required=True,
        empty_label=None,
    )
    presentation_avatar = forms.ModelChoiceField(
        queryset=Avatar.objects.filter(
            category=Avatar.Category.PRESENTATION, is_active=True
        ).order_by("name"),
        required=True,
        empty_label=None,
    )
    epistemic_avatar = forms.ModelChoiceField(
        queryset=Avatar.objects.filter(
            category=Avatar.Category.EPISTEMIC, is_active=True
        ).order_by("name"),
        required=True,
        empty_label=None,
    )
    performance_avatar = forms.ModelChoiceField(
        queryset=Avatar.objects.filter(
            category=Avatar.Category.PERFORMANCE, is_active=True
        ).order_by("name"),
        required=True,
        empty_label=None,
    )
    checkpointing_avatar = forms.ModelChoiceField(
        queryset=Avatar.objects.filter(
            category=Avatar.Category.CHECKPOINTING, is_active=True
        ).order_by("name"),
        required=True,
        empty_label=None,
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
            "default_language": forms.TextInput(attrs={"class": "form-control w-100"}),
            "default_language_variant": forms.Select(attrs={"class": "form-select w-100"}),
            "language_switching_permitted": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "persist_language_switch_for_session": forms.CheckboxInput(attrs={"class": "form-check-input"}),

            "cognitive_avatar": forms.Select(attrs={"class": "form-select w-100"}),
            "interaction_avatar": forms.Select(attrs={"class": "form-select w-100"}),
            "presentation_avatar": forms.Select(attrs={"class": "form-select w-100"}),
            "epistemic_avatar": forms.Select(attrs={"class": "form-select w-100"}),
            "performance_avatar": forms.Select(attrs={"class": "form-select w-100"}),
            "checkpointing_avatar": forms.Select(attrs={"class": "form-select w-100"}),
        }
