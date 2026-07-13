import secrets
from hashlib import sha256

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel


class APIKey(TimeStampedModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="api_keys")
    label = models.CharField(max_length=150)
    key_prefix = models.CharField(max_length=12, unique=True)
    key_hash = models.CharField(max_length=64, unique=True)
    can_view_cost = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    revoked_at = models.DateTimeField(blank=True, null=True)
    last_used_at = models.DateTimeField(blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="api_keys_created",
        blank=True,
        null=True,
    )
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "is_active"]),
            models.Index(fields=["tenant", "key_prefix"]),
        ]
        ordering = ["tenant_id", "label", "id"]

    def __str__(self):
        return f"{self.label} ({self.key_prefix})"

    @staticmethod
    def _hash(raw_key):
        return sha256(raw_key.encode("utf-8")).hexdigest()

    @classmethod
    def create_key(cls, tenant, label, created_by=None, can_view_cost=False, notes=""):
        prefix = secrets.token_hex(4)
        secret = secrets.token_urlsafe(24)
        raw_key = f"{prefix}.{secret}"
        api_key = cls.objects.create(
            tenant=tenant,
            label=label,
            key_prefix=prefix,
            key_hash=cls._hash(raw_key),
            can_view_cost=can_view_cost,
            created_by=created_by,
            notes=notes,
        )
        return api_key, raw_key

    def revoke(self):
        self.is_active = False
        self.revoked_at = timezone.now()
        self.save(update_fields=["is_active", "revoked_at", "updated_at"])

