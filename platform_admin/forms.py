from django import forms

from tenants.models import SubscriptionPlan, Tenant, TenantSubscription


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "app-field")


class TenantCreateForm(StyledFormMixin, forms.ModelForm):
    plan = forms.ModelChoiceField(queryset=SubscriptionPlan.objects.filter(is_active=True))
    billing_cycle = forms.ChoiceField(choices=TenantSubscription.BillingCycle.choices)

    class Meta:
        model = Tenant
        fields = ("name", "slug", "business_type", "contact_name", "contact_email", "contact_phone", "address", "country", "currency", "timezone")


class PlanForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = SubscriptionPlan
        fields = ("name", "code", "monthly_price", "annual_price", "trial_days", "max_users", "feature_limits", "is_active")
        widgets = {"feature_limits": forms.Textarea(attrs={"rows": 3})}


class PlanChangeForm(StyledFormMixin, forms.Form):
    plan = forms.ModelChoiceField(queryset=SubscriptionPlan.objects.filter(is_active=True))
    billing_cycle = forms.ChoiceField(choices=TenantSubscription.BillingCycle.choices)


class TrialExtensionForm(StyledFormMixin, forms.Form):
    days = forms.IntegerField(min_value=1, max_value=365)
