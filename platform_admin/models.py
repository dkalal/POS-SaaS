from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class PlatformAuditLog(TimeStampedModel):
    class Action(models.TextChoices):
        TENANT_CREATED = "tenant_created", "Tenant created"
        TENANT_ACTIVATED = "tenant_activated", "Tenant activated"
        TENANT_SUSPENDED = "tenant_suspended", "Tenant suspended"
        TENANT_CANCELLED = "tenant_cancelled", "Tenant cancelled"
        TRIAL_EXTENDED = "trial_extended", "Trial extended"
        PLAN_CHANGED = "plan_changed", "Plan changed"
        PLAN_CREATED = "plan_created", "Plan created"
        PLAN_UPDATED = "plan_updated", "Plan updated"
        PLAN_DISABLED = "plan_disabled", "Plan disabled"

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="platform_audit_logs")
    target_tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.PROTECT, related_name="platform_audit_logs", blank=True, null=True
    )
    action = models.CharField(max_length=32, choices=Action.choices)
    before_data = models.JSONField(default=dict, blank=True)
    after_data = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["target_tenant", "action"]), models.Index(fields=["created_at"])]

    def __str__(self):
        return f"{self.get_action_display()} by {self.actor}"
