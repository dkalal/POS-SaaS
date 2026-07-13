from django.db import models

from core.models import ActiveTenantScopedModel


class Category(ActiveTenantScopedModel):
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=160)
    description = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("tenant", "slug")
        indexes = [
            models.Index(fields=["tenant", "slug"]),
            models.Index(fields=["tenant", "is_active"]),
            models.Index(fields=["tenant", "name"]),
        ]
        ordering = ["sort_order", "name"]


class Product(ActiveTenantScopedModel):
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products", null=True, blank=True)
    name = models.CharField(max_length=255)
    sku = models.CharField(max_length=64)
    barcode = models.CharField(max_length=64, blank=True, null=True)
    description = models.TextField(blank=True)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sale_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    reorder_level = models.PositiveIntegerField(default=0)
    track_inventory = models.BooleanField(default=True)

    class Meta:
        unique_together = (("tenant", "sku"), ("tenant", "barcode"))
        indexes = [
            models.Index(fields=["sku"]),
            models.Index(fields=["barcode"]),
            models.Index(fields=["tenant", "sku"]),
            models.Index(fields=["tenant", "is_active"]),
            models.Index(fields=["tenant", "name"]),
            models.Index(fields=["tenant", "barcode"]),
            models.Index(fields=["tenant", "category"]),
        ]
        ordering = ["name", "id"]

