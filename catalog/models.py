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

    def __str__(self):
        return self.name


class Product(ActiveTenantScopedModel):
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products", null=True, blank=True)
    name = models.CharField(max_length=255)
    sku = models.CharField(max_length=64, blank=True)
    barcode = models.CharField(max_length=64, blank=True, null=True)
    description = models.TextField(blank=True)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sale_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    reorder_level = models.PositiveIntegerField(default=0)
    track_inventory = models.BooleanField(default=True)

    class Meta:
        unique_together = (("tenant", "barcode"),)
        constraints = [
            models.UniqueConstraint(fields=["tenant", "sku"], name="unique_product_sku_per_tenant"),
        ]
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

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Keep imports local so the reusable SKU service can query Product.
        from catalog.services import generate_product_sku, normalize_sku

        if self.sku:
            self.sku = normalize_sku(self.sku)
        elif self.tenant_id:
            self.sku = generate_product_sku(
                tenant=self.tenant,
                category=self.category,
                name=self.name,
                exclude_product_id=self.pk,
            )
        return super().save(*args, **kwargs)
