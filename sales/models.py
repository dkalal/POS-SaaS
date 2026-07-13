from django.conf import settings
from django.db import models

from core.models import TenantScopedModel, TimeStampedModel


class Sale(TenantScopedModel):
    class Status(models.TextChoices):
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    sale_number = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.COMPLETED)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=14, decimal_places=2)
    notes = models.TextField(blank=True)
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales_as_cashier")
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales_cancelled", blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    cancel_reason = models.TextField(blank=True)

    class Meta:
        unique_together = ("tenant", "sale_number")
        indexes = [
            models.Index(fields=["tenant", "sale_number"]),
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "cashier"]),
            models.Index(fields=["created_at"]),
        ]
        ordering = ["-created_at", "-id"]


class SaleItem(TenantScopedModel):
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="sale_items")
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost_snapshot = models.DecimalField(max_digits=12, decimal_places=2)
    line_subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "sale"]),
            models.Index(fields=["tenant", "product"]),
        ]
        ordering = ["id"]


class Receipt(TenantScopedModel):
    sale = models.OneToOneField(Sale, on_delete=models.PROTECT, related_name="receipt")
    receipt_number = models.CharField(max_length=64)
    issued_at = models.DateTimeField(auto_now_add=True)
    printed_at = models.DateTimeField(blank=True, null=True)
    receipt_data = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("tenant", "receipt_number")
        indexes = [
            models.Index(fields=["tenant", "receipt_number"]),
            models.Index(fields=["tenant", "sale"]),
        ]
        ordering = ["-issued_at", "-id"]

