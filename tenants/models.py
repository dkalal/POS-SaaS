from django.db import models

from core.models import TimeStampedModel


class Tenant(TimeStampedModel):
    class Status(models.TextChoices):
        TRIAL = "trial", "Trial"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        CANCELLED = "cancelled", "Cancelled"

    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    business_type = models.CharField(max_length=100, blank=True)
    contact_name = models.CharField(max_length=255, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=64, blank=True)
    address = models.TextField(blank=True)
    country = models.CharField(max_length=2, default="TZ")
    currency = models.CharField(max_length=3, default="TZS")
    timezone = models.CharField(max_length=64, default="Africa/Dar_es_Salaam")
    receipt_prefix = models.CharField(max_length=16, default="POS")
    default_tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    default_track_inventory = models.BooleanField(default=True)
    receipt_business_details = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.TRIAL)
    trial_ends_at = models.DateTimeField(blank=True, null=True)
    subscription_plan = models.ForeignKey(
        "SubscriptionPlan", on_delete=models.PROTECT, related_name="current_tenants", blank=True, null=True
    )
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class SubscriptionPlan(TimeStampedModel):
    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=64, unique=True)
    monthly_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    annual_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    trial_days = models.PositiveIntegerField(default=14)
    max_users = models.PositiveIntegerField(default=5)
    feature_limits = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["monthly_price", "name"]

    def __str__(self):
        return self.name


class TenantSubscription(TimeStampedModel):
    class Status(models.TextChoices):
        TRIAL = "trial", "Trial"
        ACTIVE = "active", "Active"
        CANCELLED = "cancelled", "Cancelled"

    class BillingCycle(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        ANNUAL = "annual", "Annual"

    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="subscriptions")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.TRIAL)
    billing_cycle = models.CharField(max_length=16, choices=BillingCycle.choices, default=BillingCycle.MONTHLY)
    started_at = models.DateTimeField()
    current_period_ends_at = models.DateTimeField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    internal_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        indexes = [models.Index(fields=["tenant", "status"])]

    def __str__(self):
        return f"{self.tenant} — {self.plan}"


from tenants.onboarding_models import OnboardingProgress  # noqa: E402,F401
