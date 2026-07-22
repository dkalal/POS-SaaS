from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class AppendOnlyPlatformAuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("PlatformAuditLog is append-only and cannot be updated.")

    def delete(self):
        raise ValueError("PlatformAuditLog is append-only and cannot be deleted.")

    def bulk_create(self, objs, **kwargs):
        raise ValueError("PlatformAuditLog must be inserted through its integrity service.")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValueError("PlatformAuditLog is append-only and cannot be updated.")


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
    previous_hash = models.CharField(max_length=64, blank=True, default="", editable=False)
    integrity_hash = models.CharField(max_length=64, blank=True, default="", editable=False)
    hash_version = models.PositiveSmallIntegerField(default=1, editable=False)

    objects = AppendOnlyPlatformAuditQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["target_tenant", "action"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["integrity_hash"], name="platform_audit_hash_idx"),
        ]

    def __str__(self):
        return f"{self.get_action_display()} by {self.actor}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("PlatformAuditLog is append-only and cannot be updated.")
        if not self.integrity_hash:
            raise ValueError("PlatformAuditLog must be created through the platform audit service.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("PlatformAuditLog is append-only and cannot be deleted.")
