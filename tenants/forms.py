from django import forms


class TenantBootstrapForm(forms.Form):
    tenant_name = forms.CharField(
        max_length=255,
        label="Tenant name",
        help_text="This is the customer-facing name for the first workspace.",
    )
    tenant_slug = forms.SlugField(
        max_length=100,
        required=False,
        label="Tenant slug",
        help_text="Optional. Leave blank and we will generate a clean slug from the name.",
    )
    api_key_label = forms.CharField(
        max_length=150,
        label="Initial API key label",
        help_text="A short name for the first integration key, such as POS sync or ERP bridge.",
    )
    api_key_can_view_cost = forms.BooleanField(
        required=False,
        initial=False,
        label="Allow cost price access",
        help_text="Enable this only for trusted integrations that need purchase or margin data.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ("tenant_name", "tenant_slug", "api_key_label"):
            self.fields[field_name].widget.attrs.update(
                {
                    "class": "setup-input",
                    "autocomplete": "off",
                }
            )
        self.fields["tenant_name"].widget.attrs.update({"placeholder": "Alpha Traders"})
        self.fields["tenant_slug"].widget.attrs.update({"placeholder": "alpha-traders"})
        self.fields["api_key_label"].widget.attrs.update({"placeholder": "Initial POS Sync"})
