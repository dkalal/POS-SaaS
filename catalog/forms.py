from django import forms

from catalog.models import Category, Product
from catalog.services import generate_product_sku, normalize_sku


def _style_text_field(field):
    field.widget.attrs.update(
        {
            "class": "app-field",
        }
    )


def _style_checkbox_field(field):
        field.widget.attrs.update(
            {
                "class": "app-check",
            }
        )


class TenantBoundModelForm(forms.ModelForm):
    def __init__(self, *args, tenant=None, **kwargs):
        self.tenant = tenant
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if getattr(field.widget, "input_type", "") == "checkbox":
                _style_checkbox_field(field)
            else:
                _style_text_field(field)


class CatalogFilterForm(forms.Form):
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
                "placeholder": "Search by name, SKU, slug, or barcode",
            }
        )
        self.fields["status"].widget.attrs.update(
            {
                "class": "app-field",
            }
        )


class CategoryForm(TenantBoundModelForm):
    class Meta:
        model = Category
        fields = ["name", "slug", "description", "sort_order", "is_active"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, tenant=tenant, **kwargs)
        self.instance.tenant = tenant
        self.fields["name"].help_text = "Displayed to staff when grouping products."
        self.fields["slug"].help_text = "URL-safe identifier, such as groceries or beverages."
        self.fields["sort_order"].help_text = "Lower numbers appear first."

    def clean_slug(self):
        return self.cleaned_data["slug"].strip().lower()


class ProductForm(TenantBoundModelForm):
    class Meta:
        model = Product
        fields = [
            "category",
            "name",
            "sku",
            "barcode",
            "description",
            "cost_price",
            "sale_price",
            "reorder_level",
            "track_inventory",
            "is_active",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, tenant=tenant, **kwargs)
        self.instance.tenant = tenant
        self.fields["category"].queryset = (
            Category.objects.filter(tenant=tenant).order_by("-is_active", "name", "id") if tenant is not None else Category.objects.none()
        )
        self.fields["category"].required = False
        self.fields["sku"].required = False
        self.fields["sku"].help_text = "Leave blank to auto-generate. Must be unique within this business."
        self.fields["barcode"].help_text = "Optional barcode for scanning, separate from SKU."
        self.fields["track_inventory"].help_text = "Turn this off for non-stock items and services."
        self.fields["is_active"].help_text = "Inactive products stay in the catalog but are hidden from daily operations."

    def clean_barcode(self):
        barcode = (self.cleaned_data.get("barcode") or "").strip()
        return barcode or None

    def clean_sku(self):
        return normalize_sku(self.cleaned_data.get("sku"))

    def clean(self):
        cleaned_data = super().clean()
        sku = cleaned_data.get("sku")
        if not sku and self.tenant is not None and cleaned_data.get("name"):
            sku = generate_product_sku(
                tenant=self.tenant,
                category=cleaned_data.get("category"),
                name=cleaned_data["name"],
                exclude_product_id=self.instance.pk,
            )
            cleaned_data["sku"] = sku

        if sku and self.tenant is not None:
            duplicates = Product.objects.filter(tenant=self.tenant, sku=sku)
            if self.instance.pk:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                self.add_error("sku", "A product with this SKU already exists in this business.")
        return cleaned_data
