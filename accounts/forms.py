from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm

from accounts.models import TenantInvitation


class SaaSAuthenticationForm(AuthenticationForm):
    username = forms.CharField(
        label="Username or email",
        widget=forms.TextInput(
            attrs={
                "class": "app-field",
                "autocomplete": "username",
                "placeholder": "owner@example.com",
                "autofocus": True,
            }
        ),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "app-field",
                "autocomplete": "current-password",
                "placeholder": "Enter your password",
            }
        ),
    )

    error_messages = {
        "invalid_login": "The credentials did not match an active account.",
        "inactive": "This account is inactive. Contact an owner/admin before trying again.",
    }

    def clean_username(self):
        identifier = self.cleaned_data["username"].strip()
        if "@" not in identifier:
            return identifier

        UserModel = get_user_model()
        matching_users = UserModel._default_manager.filter(email__iexact=identifier, is_active=True)
        if matching_users.count() == 1:
            return matching_users.get().get_username()
        return identifier


class TenantInvitationForm(forms.Form):
    email = forms.EmailField(
        label="Email address",
        help_text="The invited person must sign in with this email to accept the membership.",
    )
    role = forms.ChoiceField(
        choices=TenantInvitation.Role.choices,
        label="Role",
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Notes",
        help_text="Optional context for the invitee.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].widget.attrs.update(
            {
                "class": "app-field",
                "placeholder": "manager@example.com",
                "autocomplete": "email",
            }
        )
        self.fields["role"].widget.attrs.update(
            {
                "class": "app-field",
            }
        )
        self.fields["notes"].widget.attrs.update(
            {
                "class": "app-field app-textarea",
                "placeholder": "Optional note for the invitation",
            }
        )

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()
