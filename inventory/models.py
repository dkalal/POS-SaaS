from django.conf import settings
from django.db import models

from core.models import TenantScopedModel, TimeStampedModel


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, *args, **kwargs):
        raise ValueError("StockMovement is append-only and cannot be updated.")

    def delete(self):
        raise ValueError("StockMovement is append-only and cannot be deleted.")


class AppendOnlyManager(models.Manager):
    def get_queryset(self):
        return AppendOnlyQuerySet(self.model, using=self._db)


class Stock(TenantScopedModel):
    product = models.OneToOneField("catalog.Product", on_delete=models.PROTECT, related_name="stock")
    quantity = models.IntegerField(default=0)
    cost_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    last_movement_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(quantity__gte=0), name="stock_quantity_non_negative"),
            models.UniqueConstraint(fields=["tenant", "product"], name="unique_stock_per_tenant_product"),
        ]
        indexes = [
            models.Index(fields=["tenant", "product"]),
            models.Index(fields=["tenant", "quantity"]),
        ]
        ordering = ["tenant_id", "product_id"]


class StockMovement(TimeStampedModel):
    class MovementType(models.TextChoices):
        PURCHASE_IN = "purchase_in", "Purchase In"
        SALE_OUT = "sale_out", "Sale Out"
        PURCHASE_REVERSAL = "purchase_reversal", "Purchase Reversal"
        SALE_REVERSAL = "sale_reversal", "Sale Reversal"
        ADJUSTMENT_IN = "adjustment_in", "Adjustment In"
        ADJUSTMENT_OUT = "adjustment_out", "Adjustment Out"

    class ReferenceType(models.TextChoices):
        PURCHASE = "purchase", "Purchase"
        SALE = "sale", "Sale"
        STOCK_ADJUSTMENT = "stock_adjustment", "Stock Adjustment"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT)
    stock = models.ForeignKey(Stock, on_delete=models.PROTECT, related_name="movements")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="stock_movements")
    movement_type = models.CharField(max_length=32, choices=MovementType.choices)
    reference_type = models.CharField(max_length=32, choices=ReferenceType.choices)
    reference_id = models.PositiveBigIntegerField()
    quantity_delta = models.IntegerField()
    quantity_before = models.IntegerField()
    quantity_after = models.IntegerField()
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_movements_created")

    objects = AppendOnlyManager()

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "stock"]),
            models.Index(fields=["tenant", "product"]),
            models.Index(
                fields=["tenant", "product", "created_at"],
                name="stock_tenant_product_time_idx",
            ),
            models.Index(fields=["tenant", "movement_type"]),
            models.Index(fields=["tenant", "reference_type", "reference_id"]),
            models.Index(fields=["created_at"]),
        ]
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("StockMovement is append-only and cannot be updated.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("StockMovement is append-only and cannot be deleted.")


class StockAdjustment(TenantScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        CANCELLED = "cancelled", "Cancelled"

    adjustment_number = models.CharField(max_length=64)
    reason = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_adjustments_created")
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_adjustments_posted", blank=True, null=True)
    posted_at = models.DateTimeField(blank=True, null=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_adjustments_cancelled", blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    cancel_reason = models.TextField(blank=True)

    class Meta:
        unique_together = ("tenant", "adjustment_number")
        indexes = [
            models.Index(fields=["tenant", "adjustment_number"]),
            models.Index(fields=["tenant", "status"]),
        ]
        ordering = ["-id"]

    def __str__(self):
        return self.adjustment_number


class StockAdjustmentItem(TenantScopedModel):
    adjustment = models.ForeignKey(StockAdjustment, on_delete=models.PROTECT, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    quantity_before = models.IntegerField()
    quantity_after = models.IntegerField()
    quantity_delta = models.IntegerField()
    note = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "adjustment"]),
            models.Index(fields=["tenant", "product"]),
        ]
        ordering = ["id"]
