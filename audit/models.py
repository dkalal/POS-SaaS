from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from core.models import TenantScopedModel


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
        INVITATION_CREATED = "invitation_created", "Invitation created"
        INVITATION_ACCEPTED = "invitation_accepted", "Invitation accepted"
        INVITATION_REVOKED = "invitation_revoked", "Invitation revoked"
        MEMBER_SUSPENDED = "member_suspended", "Member suspended"
        MEMBER_REMOVED = "member_removed", "Member removed"
        WORKSPACE_SWITCHED = "workspace_switched", "Workspace switched"

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="audit_events")
    action = models.CharField(max_length=64, choices=Action.choices)
    target_content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    target_object_id = models.CharField(max_length=64)
    target = GenericForeignKey("target_content_type", "target_object_id")
    target_label = models.CharField(max_length=255)
    before_data = models.JSONField(default=dict, blank=True)
    after_data = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "action"]),
            models.Index(fields=["tenant", "target_content_type", "target_object_id"]),
            models.Index(fields=["tenant", "created_at"]),
        ]
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_action_display()} - {self.target_label}"
