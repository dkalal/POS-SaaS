from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
import hashlib

from core.models import TenantScopedModel


class TenantMembership(TenantScopedModel):
    class Status(models.TextChoices):
        INVITED = "invited", "Invited"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        REMOVED = "removed", "Removed"

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        OWNER_ADMIN = "owner_admin", "Owner/Admin"
        MANAGER = "manager", "Manager"
        CASHIER = "cashier", "Cashier"
        VIEWER = "viewer", "Viewer"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tenant_memberships")
    role = models.CharField(max_length=32, choices=Role.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="memberships_invited", blank=True, null=True)
    joined_at = models.DateTimeField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("tenant", "user")
        indexes = [
            models.Index(fields=["tenant", "user"]),
            models.Index(fields=["tenant", "role"]),
            models.Index(fields=["tenant", "is_active"]),
            models.Index(
                fields=["user", "status", "is_active", "tenant"],
                name="accounts_mem_resolve_idx",
            ),
        ]
        ordering = ["tenant_id", "user_id"]

    def clean(self):
        super().clean()
        if self.user_id and getattr(self.user, "is_superuser", False):
            raise ValidationError("Platform administrators cannot belong to a tenant workspace.")

    def save(self, *args, **kwargs):
        if self.user_id and getattr(self.user, "is_superuser", False):
            raise ValidationError("Platform administrators cannot belong to a tenant workspace.")
        if self.status in (self.Status.SUSPENDED, self.Status.REMOVED, self.Status.INVITED):
            self.is_active = False
        elif self.status == self.Status.ACTIVE:
            self.is_active = True
        if self.is_active and not self.joined_at:
            self.joined_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} - {self.get_role_display()}"


class TenantInvitation(TenantScopedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    class Role(models.TextChoices):
        MANAGER = TenantMembership.Role.MANAGER, "Manager"
        CASHIER = TenantMembership.Role.CASHIER, "Cashier"
        ADMIN = TenantMembership.Role.ADMIN, "Admin"
        VIEWER = TenantMembership.Role.VIEWER, "Viewer"

    email = models.EmailField()
    role = models.CharField(max_length=32, choices=Role.choices)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
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
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    revoked_at = models.DateTimeField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "email"]),
            models.Index(fields=["tenant", "role"]),
            models.Index(fields=["tenant", "is_active"]),
            models.Index(fields=["token_hash"]),
        ]
        ordering = ["-invited_at", "-id"]

    def __str__(self):
        return f"{self.email} ({self.get_role_display()})"

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    def __init__(self, *args, **kwargs):
        raw_token = kwargs.pop("token", None)
        super().__init__(*args, **kwargs)
        self._raw_token = raw_token
        if raw_token and not self.token_hash:
            self.token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    @property
    def token(self):
        """Compatibility accessor for the one request that creates an invite URL."""
        return getattr(self, "_raw_token", "")


Invitation = TenantInvitation


class EmailVerification(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="email_verification")
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(blank=True, null=True)
    last_sent_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_verified(self):
        return self.verified_at is not None

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at
