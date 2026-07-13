from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TenantScopedModel


class TenantMembership(TenantScopedModel):
    class Role(models.TextChoices):
        OWNER_ADMIN = "owner_admin", "Owner/Admin"
        MANAGER = "manager", "Manager"
        CASHIER = "cashier", "Cashier"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tenant_memberships")
    role = models.CharField(max_length=32, choices=Role.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("tenant", "user")
        indexes = [
            models.Index(fields=["tenant", "user"]),
            models.Index(fields=["tenant", "role"]),
            models.Index(fields=["tenant", "is_active"]),
        ]
        ordering = ["tenant_id", "user_id"]

    def __str__(self):
        return f"{self.user} - {self.get_role_display()}"


class TenantInvitation(TenantScopedModel):
    class Role(models.TextChoices):
        MANAGER = TenantMembership.Role.MANAGER, "Manager"
        CASHIER = TenantMembership.Role.CASHIER, "Cashier"

    email = models.EmailField()
    role = models.CharField(max_length=32, choices=Role.choices)
    token = models.CharField(max_length=64, unique=True, editable=False)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="tenant_invitations_sent",
    )
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="tenant_invitations_accepted",
        blank=True,
        null=True,
    )
    notes = models.TextField(blank=True)
    invited_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(blank=True, null=True)
    revoked_at = models.DateTimeField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "email"]),
            models.Index(fields=["tenant", "role"]),
            models.Index(fields=["tenant", "is_active"]),
            models.Index(fields=["token"]),
        ]
        ordering = ["-invited_at", "-id"]

    def __str__(self):
        return f"{self.email} ({self.get_role_display()})"

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at
