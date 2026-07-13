from django.db import models

from core.models import ActiveTenantScopedModel


class Supplier(ActiveTenantScopedModel):
    name = models.CharField(max_length=255)
    supplier_code = models.CharField(max_length=64, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = ("tenant", "name")
        indexes = [
            models.Index(fields=["tenant", "name"]),
            models.Index(fields=["tenant", "is_active"]),
            models.Index(fields=["tenant", "supplier_code"]),
        ]
        ordering = ["name", "id"]

