from decimal import Decimal

from django import forms
from django.forms import BaseFormSet, formset_factory
from django.db import models

from catalog.models import Product
from inventory.models import StockAdjustment


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


class AdjustmentFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Search")
    status = forms.ChoiceField(
        required=False,
        choices=(("", "All"),) + tuple(StockAdjustment.Status.choices),
        label="Status",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["q"].widget.attrs.update(
            {
                "class": "app-field",
                "placeholder": "Search by adjustment number, reason, or notes",
            }
        )
        self.fields["status"].widget.attrs.update(
            {
                "class": "app-field",
            }
        )


class StockAdjustmentForm(forms.ModelForm):
    class Meta:
        model = StockAdjustment
        fields = ["reason", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, tenant=None, **kwargs):
        self.tenant = tenant
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            _style_field(field)
        self.fields["reason"].help_text = "Short reason for the adjustment, such as stock count or spoilage."
        self.fields["notes"].help_text = "Optional internal details for the audit trail."

    def save(self, commit=True):  # type: ignore[override]
        adjustment = super().save(commit=False)
        if self.tenant is not None:
            adjustment.tenant = self.tenant
        if commit:
            adjustment.save()
        return adjustment


class StockAdjustmentLineForm(forms.Form):
    class Direction(models.TextChoices):
        INCREASE = "increase", "Increase"
        DECREASE = "decrease", "Decrease"

    product = forms.ModelChoiceField(queryset=Product.objects.none(), label="Product")
    direction = forms.ChoiceField(choices=Direction.choices, label="Direction")
    quantity = forms.IntegerField(min_value=1, label="Quantity")
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Note")

    def __init__(self, *args, tenant=None, **kwargs):
        self.tenant = tenant
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = (
            Product.objects.filter(tenant=tenant).select_related("category").order_by("name", "sku")
            if tenant is not None
            else Product.objects.none()
        )
        self.fields["product"].help_text = "Choose any tenant product that needs a stock correction."
        self.fields["direction"].help_text = "Increase adds stock; decrease removes stock."
        self.fields["quantity"].help_text = "Enter the quantity to adjust."
        self.fields["note"].help_text = "Optional line note for the audit trail."
        for field in self.fields.values():
            _style_field(field)


class BaseStockAdjustmentLineFormSet(BaseFormSet):
    def clean(self):
        if any(self.errors):
            return
        seen_products = set()
        has_line = False
        for form in self.forms:
            cleaned = getattr(form, "cleaned_data", None) or {}
            product = cleaned.get("product")
            quantity = cleaned.get("quantity")
            direction = cleaned.get("direction")
            if product is None:
                continue
            has_line = True
            product_id = product.pk
            if product_id in seen_products:
                raise forms.ValidationError("Each product can appear only once in a stock adjustment.")
            seen_products.add(product_id)
            if quantity is None or quantity <= 0:
                raise forms.ValidationError("Each adjustment line needs a positive quantity.")
            if direction not in (StockAdjustmentLineForm.Direction.INCREASE, StockAdjustmentLineForm.Direction.DECREASE):
                raise forms.ValidationError("Choose a valid adjustment direction.")
        if not has_line:
            raise forms.ValidationError("Add at least one stock adjustment line.")


StockAdjustmentLineFormSet = formset_factory(StockAdjustmentLineForm, formset=BaseStockAdjustmentLineFormSet, extra=5)
