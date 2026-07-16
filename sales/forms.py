from decimal import Decimal

from django import forms

from payments.models import Payment


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
    discount = forms.DecimalField(
        required=False,
        min_value=Decimal("0.00"),
        decimal_places=2,
        max_digits=14,
        initial=Decimal("0.00"),
        label="Discount",
    )
    tax = forms.DecimalField(
        required=False,
        min_value=Decimal("0.00"),
        decimal_places=2,
        max_digits=14,
        initial=Decimal("0.00"),
        label="Tax",
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
