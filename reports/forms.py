from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.utils import timezone

from accounts.models import TenantMembership
from catalog.models import Category, Product
from payments.models import Payment
from sales.models import Customer
from suppliers.models import Supplier


PERIOD_CHOICES = (
    ("today", "Today"),
    ("week", "This week"),
    ("month", "This month"),
    ("year", "This year"),
    ("custom", "Custom range"),
)


class ReportFilterForm(forms.Form):
    period = forms.ChoiceField(choices=PERIOD_CHOICES, initial="month")
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, tenant, include=(), **kwargs):
        self.tenant = tenant
        super().__init__(*args, **kwargs)
        include = set(include)
        if "supplier" in include:
            self.fields["supplier"] = forms.ModelChoiceField(
                queryset=Supplier.objects.filter(tenant=tenant).order_by("name"), required=False
            )
        if "category" in include:
            self.fields["category"] = forms.ModelChoiceField(
                queryset=Category.objects.filter(tenant=tenant).order_by("name"), required=False
            )
        if "product" in include:
            self.fields["product"] = forms.ModelChoiceField(
                queryset=Product.objects.filter(tenant=tenant).order_by("name"), required=False
            )
        if "cashier" in include:
            self.fields["cashier"] = forms.ModelChoiceField(
                queryset=TenantMembership.objects.filter(tenant=tenant, is_active=True).select_related("user"),
                required=False,
                label="Cashier",
            )
            self.fields["cashier"].label_from_instance = lambda membership: (
                membership.user.get_full_name() or membership.user.get_username()
            )
        if "customer" in include:
            self.fields["customer"] = forms.ModelChoiceField(
                queryset=Customer.objects.filter(tenant=tenant).order_by("name"), required=False
            )
        if "payment_method" in include:
            self.fields["payment_method"] = forms.ChoiceField(
                choices=(("", "All payment methods"), *Payment.Method.choices), required=False
            )
        if "stock_status" in include:
            self.fields["stock_status"] = forms.ChoiceField(
                choices=(("", "All stock statuses"), ("in_stock", "In stock"), ("low_stock", "Low stock"), ("out_of_stock", "Out of stock")),
                required=False,
            )

    def clean(self):
        cleaned = super().clean()
        try:
            tenant_zone = ZoneInfo(self.tenant.timezone)
        except ZoneInfoNotFoundError:
            tenant_zone = timezone.get_default_timezone()
        today = timezone.localdate(timezone=tenant_zone)
        period = cleaned.get("period") or "month"
        if period == "today":
            start = end = today
        elif period == "week":
            start, end = today - timedelta(days=today.weekday()), today
        elif period == "month":
            start, end = today.replace(day=1), today
        elif period == "year":
            start, end = today.replace(month=1, day=1), today
        else:
            start, end = cleaned.get("date_from"), cleaned.get("date_to")
            if not start or not end:
                raise forms.ValidationError("Choose both start and end dates for a custom range.")
        if start > end:
            raise forms.ValidationError("The start date must be on or before the end date.")
        if (end - start).days > 366:
            raise forms.ValidationError("Choose a date range of 367 days or fewer.")
        cleaned["range_start"] = start
        cleaned["range_end"] = end
        return cleaned

    def datetime_bounds(self):
        try:
            zone = ZoneInfo(self.tenant.timezone)
        except ZoneInfoNotFoundError:
            zone = timezone.get_default_timezone()
        start = timezone.make_aware(datetime.combine(self.cleaned_data["range_start"], time.min), zone)
        end = timezone.make_aware(datetime.combine(self.cleaned_data["range_end"] + timedelta(days=1), time.min), zone)
        return start, end

    @property
    def range_label(self):
        return f"{self.cleaned_data['range_start']:%Y-%m-%d} to {self.cleaned_data['range_end']:%Y-%m-%d}"
