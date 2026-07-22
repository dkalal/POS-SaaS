from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from core.managers import TenantAwareManager
from core.models import AppendOnlyTenantQuerySet, TenantScopedModel


class AuditEvent(TenantScopedModel):
    class Action(models.TextChoices):
        PURCHASE_CREATED = "purchase_created", "Purchase created"
        PURCHASE_UPDATED = "purchase_updated", "Purchase updated"
        PURCHASE_DUPLICATED = "purchase_duplicated", "Purchase duplicated"
        PURCHASE_RECEIVED = "purchase_received", "Purchase received"
        PURCHASE_CANCELLED = "purchase_cancelled", "Purchase cancelled"
        STOCK_ADJUSTMENT_CREATED = "stock_adjustment_created", "Stock adjustment created"
        STOCK_ADJUSTMENT_UPDATED = "stock_adjustment_updated", "Stock adjustment updated"
        STOCK_ADJUSTMENT_POSTED = "stock_adjustment_posted", "Stock adjustment posted"
        STOCK_ADJUSTMENT_CANCELLED = "stock_adjustment_cancelled", "Stock adjustment cancelled"
        ROLE_ASSIGNED = "role_assigned", "Role assigned"
        ROLE_UPDATED = "role_updated", "Role updated"
        WORKSPACE_CREATED = "workspace_created", "Workspace created"
        ONBOARDING_DISMISSED = "onboarding_dismissed", "Onboarding dismissed"
        ONBOARDING_RESUMED = "onboarding_resumed", "Onboarding resumed"
        ONBOARDING_COMPLETED = "onboarding_completed", "Onboarding completed"
        INVITATION_CREATED = "invitation_created", "Invitation created"
        INVITATION_ACCEPTED = "invitation_accepted", "Invitation accepted"
        INVITATION_REVOKED = "invitation_revoked", "Invitation revoked"
        INVITATION_RESENT = "invitation_resent", "Invitation resent"
        MEMBER_SUSPENDED = "member_suspended", "Member suspended"
        MEMBER_REACTIVATED = "member_reactivated", "Member reactivated"
        MEMBER_REMOVED = "member_removed", "Member removed"
        WORKSPACE_SETTINGS_UPDATED = "workspace_settings_updated", "Workspace settings updated"
        WORKSPACE_SWITCHED = "workspace_switched", "Workspace switched"
        QUOTATION_CREATED = "quotation_created", "Quotation created"
        QUOTATION_UPDATED = "quotation_updated", "Quotation updated"
        QUOTATION_STATUS_CHANGED = "quotation_status_changed", "Quotation status changed"
        QUOTATION_CONVERTED = "quotation_converted", "Quotation converted"
        RECEIPT_PRINTED = "receipt_printed", "Receipt printed"

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="audit_events")
    action = models.CharField(max_length=64, choices=Action.choices)
    target_content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    target_object_id = models.CharField(max_length=64)
    target = GenericForeignKey("target_content_type", "target_object_id")
    target_label = models.CharField(max_length=255)
    before_data = models.JSONField(default=dict, blank=True)
    after_data = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    previous_hash = models.CharField(max_length=64, blank=True, default="", editable=False)
    integrity_hash = models.CharField(max_length=64, blank=True, default="", editable=False)
    hash_version = models.PositiveSmallIntegerField(default=1, editable=False)

    objects = TenantAwareManager.from_queryset(AppendOnlyTenantQuerySet)()

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "action"]),
            models.Index(fields=["tenant", "target_content_type", "target_object_id"]),
            models.Index(fields=["tenant", "created_at"]),
            models.Index(fields=["tenant", "integrity_hash"], name="audit_tenant_hash_idx"),
        ]
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_action_display()} - {self.target_label}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("AuditEvent is append-only and cannot be updated.")
        if not self.integrity_hash:
            raise ValueError("AuditEvent must be created through log_audit_event().")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AuditEvent is append-only and cannot be deleted.")
