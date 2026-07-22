from django.conf import settings
from django.db import models

from core.models import TenantScopedModel, TimeStampedModel


class Purchase(TenantScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"

    purchase_number = models.CharField(max_length=64)
    supplier = models.ForeignKey("suppliers.Supplier", on_delete=models.PROTECT, related_name="purchases")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    order_date = models.DateField()
    expected_date = models.DateField(blank=True, null=True)
    received_date = models.DateTimeField(blank=True, null=True)
    cancelled_date = models.DateTimeField(blank=True, null=True)
    cancelled_reason = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchases_created")
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchases_received", blank=True, null=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchases_cancelled", blank=True, null=True)

    class Meta:
        unique_together = ("tenant", "purchase_number")
        indexes = [
            models.Index(fields=["tenant", "purchase_number"]),
            models.Index(fields=["tenant", "supplier"]),
            models.Index(fields=["tenant", "status"]),
            models.Index(
                fields=["tenant", "status", "received_date"],
                name="purchase_tenant_recv_idx",
            ),
            models.Index(fields=["tenant", "order_date"]),
        ]
        ordering = ["-order_date", "-id"]

    def __str__(self):
        return self.purchase_number


class PurchaseItem(TenantScopedModel):
    purchase = models.ForeignKey(Purchase, on_delete=models.PROTECT, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="purchase_items")
    quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "purchase"]),
            models.Index(fields=["tenant", "product"]),
        ]
        ordering = ["id"]
