from decimal import Decimal

from django import forms
from django.forms import BaseFormSet, formset_factory

from catalog.models import Product
from core.fields import MoneyField, MoneyInput
from purchasing.models import Purchase
from suppliers.models import Supplier


def _style_text(field):
    field.widget.attrs.update(
        {
            "class": "app-field",
        }
    )


class PurchaseFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Search")
    status = forms.ChoiceField(
        required=False,
        choices=(("", "All"),) + tuple(Purchase.Status.choices),
        label="Status",
    )
    supplier = forms.ChoiceField(required=False, label="Supplier")
    date_from = forms.DateField(required=False, label="From", widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, label="To", widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, supplier_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["q"].widget.attrs.update(
            {
                "class": "app-field",
                "placeholder": "Number, supplier, product, or note",
            }
        )
        self.fields["status"].widget.attrs.update(
            {
                "class": "app-field",
            }
        )
        self.fields["supplier"].widget.attrs.update(
            {
                "class": "app-field",
            }
        )
        self.fields["date_from"].widget.attrs.update({"class": "app-field"})
        self.fields["date_to"].widget.attrs.update({"class": "app-field"})
        self.fields["supplier"].choices = [("", "All")] + list(supplier_choices or [])


class PurchaseCreateForm(forms.ModelForm):
    class Meta:
        model = Purchase
        fields = ["supplier", "order_date", "expected_date", "notes"]
        widgets = {
            "order_date": forms.DateInput(attrs={"type": "date"}),
            "expected_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, tenant=None, **kwargs):
        self.tenant = tenant
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            _style_text(field)
        self.fields["supplier"].queryset = (
            Supplier.objects.filter(tenant=tenant, is_active=True).order_by("name", "id") if tenant is not None else Supplier.objects.none()
        )
        self.fields["supplier"].help_text = "Choose the supplier this draft purchase belongs to."
        self.fields["order_date"].help_text = "Defaults to today in the current tenant timezone."
        self.fields["expected_date"].help_text = "Optional planned receiving date."
        self.fields["notes"].help_text = "Optional internal notes for procurement."

    def save(self, commit=True):  # type: ignore[override]
        purchase = super().save(commit=False)
        if self.tenant is not None:
            purchase.tenant = self.tenant
        if commit:
            purchase.save()
        return purchase


class PurchaseItemEntryForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.none(), label="Product")
    quantity = forms.IntegerField(min_value=1, label="Quantity")
    unit_cost = MoneyField(
        min_value=Decimal("0.00"), decimal_places=2, max_digits=12, label="Unit cost", widget=MoneyInput()
    )

    def __init__(self, *args, tenant=None, **kwargs):
        self.tenant = tenant
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = (
            Product.objects.filter(tenant=tenant, is_active=True, track_inventory=True).select_related("category").order_by("sku", "name")
            if tenant is not None
            else Product.objects.none()
        )
        self.fields["product"].help_text = "Active physical products in this tenant only, ordered by SKU then name."
        self.fields["product"].label_from_instance = lambda product: " · ".join(
            value for value in (product.sku, product.name, product.barcode) if value
        )
        self.fields["quantity"].widget.attrs["placeholder"] = "1"
        self.fields["unit_cost"].widget.attrs["placeholder"] = "0.00"
        for field in self.fields.values():
            _style_text(field)


class BasePurchaseItemFormSet(BaseFormSet):
    def clean(self):
        if any(self.errors):
            return
        if not any(form.cleaned_data.get("product") for form in self.forms if hasattr(form, "cleaned_data")):
            raise forms.ValidationError("Add at least one purchase line item.")


PurchaseItemFormSet = formset_factory(PurchaseItemEntryForm, formset=BasePurchaseItemFormSet, extra=5)
