from django import forms

from suppliers.models import Supplier


def _style_field(field):
        if getattr(field.widget, "input_type", "") == "checkbox":
            field.widget.attrs.update(
                {
                    "class": "app-check",
                }
            )
        else:
            field.widget.attrs.update(
                {
                    "class": "app-field",
                }
            )


class TenantBoundModelForm(forms.ModelForm):
    def __init__(self, *args, tenant=None, **kwargs):
        self.tenant = tenant
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            _style_field(field)


class SupplierFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Search")
    status = forms.ChoiceField(
        required=False,
        choices=(
            ("", "All"),
            ("active", "Active"),
            ("inactive", "Inactive"),
        ),
        label="Status",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["q"].widget.attrs.update(
            {
                "class": "app-field",
                "placeholder": "Search by supplier name, code, email, or phone",
            }
        )
        self.fields["status"].widget.attrs.update(
            {
                "class": "app-field",
            }
        )


class SupplierForm(TenantBoundModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "supplier_code", "phone", "email", "address", "notes", "is_active"]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, tenant=tenant, **kwargs)
        self.instance.tenant = tenant
        self.fields["supplier_code"].help_text = "Optional supplier reference used by purchasing and reconciliation."
        self.fields["phone"].help_text = "Primary contact number for purchase coordination."
        self.fields["email"].help_text = "Optional. Useful for purchase orders and statements."

    def clean_supplier_code(self):
        return (self.cleaned_data.get("supplier_code") or "").strip()
