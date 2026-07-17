from decimal import Decimal, InvalidOperation

from django import forms


class MoneyField(forms.DecimalField):
    """Accept human-friendly grouped amounts while preserving Decimal values."""

    def to_python(self, value):
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return super().to_python(value)


class MoneyInput(forms.TextInput):
    input_type = "text"

    def __init__(self, attrs=None):
        defaults = {
            "inputmode": "decimal",
            "autocomplete": "off",
            "placeholder": "0.00",
            "data-money-input": "true",
        }
        if attrs:
            defaults.update(attrs)
        super().__init__(attrs=defaults)
