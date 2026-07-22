from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from accounts.models import TenantInvitation, TenantMembership
from tenants.models import SubscriptionPlan


class SignupForm(forms.Form):
    business_name = forms.CharField(max_length=255, label="Business or workspace name")
    owner_name = forms.CharField(max_length=255, label="Your full name")
    email = forms.EmailField(label="Work email")
    phone = forms.CharField(max_length=64, required=False, label="Phone number (optional)")
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password")
    plan = forms.ModelChoiceField(queryset=SubscriptionPlan.objects.none(), required=False, empty_label=None)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plan"].queryset = SubscriptionPlan.objects.filter(is_active=True)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "app-field")
        self.fields["password1"].widget.attrs.update({"autocomplete": "new-password"})
        self.fields["password2"].widget.attrs.update({"autocomplete": "new-password"})

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "We could not create a workspace with these details. Sign in if you already have an account."
            )
        return email

    def clean_business_name(self):
        name = self.cleaned_data["business_name"].strip()
        from tenants.models import Tenant
        if Tenant.objects.filter(name__iexact=name).exists():
            raise forms.ValidationError("That business name is already registered. Choose a name that identifies your workspace.")
        return name

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password1") and cleaned.get("password1") != cleaned.get("password2"):
            self.add_error("password2", "The passwords do not match.")
        if cleaned.get("password1"):
            try:
                validate_password(cleaned["password1"])
            except ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned


class BusinessProfileForm(forms.Form):
    business_name = forms.CharField(max_length=255)
    address = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    business_type = forms.CharField(max_length=100, required=False)
    currency = forms.ChoiceField(choices=(("TZS", "TZS — Tanzanian Shilling"), ("USD", "USD — US Dollar")))
    timezone = forms.CharField(initial="Africa/Dar_es_Salaam")
    receipt_business_details = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))


class POSPreferencesForm(forms.Form):
    default_tax_rate = forms.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100, initial=0)
    receipt_prefix = forms.CharField(max_length=16, initial="POS")
    default_track_inventory = forms.BooleanField(required=False, initial=True)


class FirstProductForm(forms.Form):
    name = forms.CharField(max_length=255)
    category = forms.CharField(max_length=150, required=False)
    product_type = forms.ChoiceField(choices=(("product", "Physical product"), ("service", "Service / non-stock item")))
    sale_price = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    sku = forms.CharField(max_length=64, required=False)
    opening_stock = forms.IntegerField(min_value=0, required=False, initial=0)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("product_type") == "service":
            cleaned["opening_stock"] = 0
        return cleaned


class InviteTeamForm(forms.Form):
    name = forms.CharField(max_length=255, required=False)
    email = forms.EmailField()
    role = forms.ChoiceField(choices=((TenantMembership.Role.MANAGER, "Manager"), (TenantMembership.Role.CASHIER, "Cashier")))


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
        "inactive": "The credentials did not match an active account.",
    }

    def clean_username(self):
        return self.cleaned_data["username"].strip()


class TenantInvitationForm(forms.Form):
    email = forms.EmailField(
        label="Email address",
        help_text="The invited person must sign in with this email to accept the membership.",
    )
    role = forms.ChoiceField(
        choices=(
            (TenantMembership.Role.MANAGER, "Manager"),
            (TenantMembership.Role.CASHIER, "Cashier"),
        ),
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


class MembershipRoleForm(forms.Form):
    role = forms.ChoiceField(choices=(), label="Workspace role")

    def __init__(self, *args, allowed_roles=(), **kwargs):
        super().__init__(*args, **kwargs)
        labels = dict(TenantMembership.Role.choices)
        self.fields["role"].choices = [(role, labels[role]) for role in allowed_roles]


class MembershipStatusForm(forms.Form):
    status = forms.ChoiceField(
        choices=(
            (TenantMembership.Status.ACTIVE, "Active"),
            (TenantMembership.Status.SUSPENDED, "Suspended"),
            (TenantMembership.Status.REMOVED, "Removed"),
        ),
        label="Membership status",
    )


class WorkspaceBusinessProfileForm(forms.Form):
    name = forms.CharField(max_length=255, label="Business/workspace name")
    contact_email = forms.EmailField(required=False, label="Business email")
    contact_phone = forms.CharField(max_length=64, required=False, label="Phone")
    address = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    tax_identification_number = forms.CharField(max_length=64, required=False, label="TIN")
    vat_registration_number = forms.CharField(max_length=64, required=False, label="VRN")

    def __init__(self, *args, tenant=None, **kwargs):
        self.tenant = tenant
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "app-field")

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        from tenants.models import Tenant
        matches = Tenant.objects.filter(name__iexact=name)
        if self.tenant is not None:
            matches = matches.exclude(pk=self.tenant.pk)
        if matches.exists():
            raise forms.ValidationError("Another workspace already uses this name.")
        return name


class WorkspaceRegionalForm(forms.Form):
    currency = forms.ChoiceField(choices=(("TZS", "TZS — Tanzanian Shilling"), ("USD", "USD — US Dollar")))
    timezone = forms.ChoiceField(choices=(("Africa/Dar_es_Salaam", "Africa/Dar_es_Salaam"),))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "app-field")


class WorkspaceReceiptForm(forms.Form):
    receipt_business_details = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Receipt business details")
    receipt_footer = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Receipt footer/note")
    receipt_prefix = forms.RegexField(
        regex=r"^[A-Za-z0-9-]+$",
        max_length=16,
        label="Receipt prefix",
        error_messages={"invalid": "Use only letters, numbers, and hyphens."},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "app-field")

    def clean_receipt_prefix(self):
        return self.cleaned_data["receipt_prefix"].strip().upper()


class WorkspaceOperationalForm(forms.Form):
    default_track_inventory = forms.BooleanField(required=False, label="Track inventory for new products by default")
    default_reorder_level = forms.IntegerField(min_value=0, max_value=1_000_000, label="Default reorder level for new products")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "app-field")


class InvitationAccountCreationForm(forms.Form):
    """Sets the first password for the email address bound to an invitation."""

    password1 = forms.CharField(
        label="Create password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "app-field",
                "autocomplete": "new-password",
                "placeholder": "At least 10 characters",
                "autofocus": True,
            }
        ),
    )
    password2 = forms.CharField(
        label="Confirm password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "app-field",
                "autocomplete": "new-password",
                "placeholder": "Repeat your password",
            }
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "The passwords do not match.")
        return cleaned_data

    def validate_password_for(self, user):
        password = self.cleaned_data["password1"]
        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            self.add_error("password1", exc)
            return False
        return True
