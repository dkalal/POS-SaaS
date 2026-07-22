from decimal import Decimal

from django import forms
from django.forms import BaseFormSet, formset_factory

from core.fields import MoneyField, MoneyInput
from payments.models import Payment
from sales.models import Customer


class RegisterSearchForm(forms.Form):
    q = forms.CharField(required=False, label="Search")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["q"].widget.attrs.update(
            {
                "class": "app-field",
                "placeholder": "Search SKU, product name, or barcode…",
                "autocomplete": "off",
                "autofocus": "autofocus",
            }
        )


class RegisterCartAdjustForm(forms.Form):
    product_id = forms.IntegerField(widget=forms.HiddenInput())
    quantity = forms.IntegerField(min_value=1, label="Quantity")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["quantity"].widget.attrs.update({"class": "app-field", "min": 1})


class RegisterPricingForm(forms.Form):
    discount = MoneyField(
        required=False,
        min_value=Decimal("0.00"),
        decimal_places=2,
        max_digits=14,
        initial=Decimal("0.00"),
        label="Discount",
        widget=MoneyInput(),
    )
    tax = MoneyField(
        required=False,
        min_value=Decimal("0.00"),
        decimal_places=2,
        max_digits=14,
        initial=Decimal("0.00"),
        label="Tax",
        widget=MoneyInput(),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["discount"].widget.attrs.update({"class": "app-field", "step": "0.01", "min": "0"})
        self.fields["tax"].widget.attrs.update({"class": "app-field", "step": "0.01", "min": "0"})


class RegisterCheckoutForm(forms.Form):
    payment_method = forms.ChoiceField(choices=Payment.Method.choices, label="Payment method")
    reference = forms.CharField(required=False, label="Reference")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payment_method"].widget.attrs.update({"class": "app-field"})
        self.fields["reference"].widget.attrs.update(
            {
                "class": "app-field",
                "placeholder": "Optional transaction reference",
            }
        )


class QuotationForm(forms.Form):
    customer = forms.ModelChoiceField(queryset=Customer.objects.none(), required=False, empty_label="Walk-in customer")
    expires_at = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Expiry date")
    discount = MoneyField(required=False, min_value=Decimal("0.00"), max_digits=14, decimal_places=2, initial=0)
    tax = MoneyField(required=False, min_value=Decimal("0.00"), max_digits=14, decimal_places=2, initial=0)

    def __init__(self, *args, tenant, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = Customer.objects.filter(tenant=tenant).order_by("name")
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "app-field")


class QuotationLineForm(forms.Form):
    product = forms.ModelChoiceField(queryset=None)
    quantity = forms.IntegerField(min_value=1)
    DELETE = forms.BooleanField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, tenant, **kwargs):
        super().__init__(*args, **kwargs)
        from catalog.models import Product

        self.fields["product"].queryset = Product.objects.filter(tenant=tenant, is_active=True).order_by("name", "sku")
        self.fields["product"].widget.attrs["class"] = "app-field"
        self.fields["quantity"].widget.attrs.update({"class": "app-field", "min": "1"})


class BaseQuotationLineFormSet(BaseFormSet):
    def clean(self):
        if any(self.errors):
            return
        active = [form for form in self.forms if form.cleaned_data and not form.cleaned_data.get("DELETE")]
        if not active:
            raise forms.ValidationError("Add at least one quotation item.")
        product_ids = [form.cleaned_data["product"].pk for form in active]
        if len(product_ids) != len(set(product_ids)):
            raise forms.ValidationError("Each product may appear only once; adjust its quantity instead.")


QuotationLineFormSet = formset_factory(QuotationLineForm, formset=BaseQuotationLineFormSet, extra=1, max_num=50)
