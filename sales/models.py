from django.conf import settings
from django.db import models

from core.models import TenantScopedModel, TimeStampedModel


class Sale(TenantScopedModel):
    class Status(models.TextChoices):
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    sale_number = models.CharField(max_length=64)
    checkout_key = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.COMPLETED)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=14, decimal_places=2)
    notes = models.TextField(blank=True)
    customer = models.ForeignKey("Customer", on_delete=models.PROTECT, related_name="sales", blank=True, null=True)
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales_as_cashier")
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales_cancelled", blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    cancel_reason = models.TextField(blank=True)

    class Meta:
        unique_together = ("tenant", "sale_number")
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "checkout_key"],
                condition=~models.Q(checkout_key=""),
                name="unique_sale_checkout_key_per_tenant",
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "sale_number"]),
            models.Index(fields=["tenant", "status"]),
            models.Index(
                fields=["tenant", "status", "created_at"],
                name="sales_tenant_status_time_idx",
            ),
            models.Index(fields=["tenant", "cashier"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["tenant", "checkout_key"]),
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
            models.Index(fields=["tenant", "issued_at"], name="sales_tenant_receipt_time_idx"),
        ]
        ordering = ["-issued_at", "-id"]


class Customer(TenantScopedModel):
    """An optional named customer; a null document customer represents walk-in trade."""

    name = models.CharField(max_length=160)
    phone = models.CharField(max_length=48, blank=True)
    email = models.EmailField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "name"])]
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class Quotation(TenantScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"
        CONVERTED = "converted", "Converted"

    quotation_number = models.CharField(max_length=64)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="quotations", blank=True, null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=14, decimal_places=2)
    expires_at = models.DateField(blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="quotations_created")
    converted_invoice = models.OneToOneField("Invoice", on_delete=models.PROTECT, related_name="source_quotation", blank=True, null=True)

    class Meta:
        unique_together = ("tenant", "quotation_number")
        indexes = [models.Index(fields=["tenant", "quotation_number"]), models.Index(fields=["tenant", "status"])]
        ordering = ["-created_at", "-id"]


class QuotationItem(TenantScopedModel):
    quotation = models.ForeignKey(Quotation, on_delete=models.PROTECT, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="quotation_items")
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["tenant", "quotation"])]


class Invoice(TenantScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        PAID = "paid", "Paid"

    invoice_number = models.CharField(max_length=64)
    sale = models.OneToOneField(Sale, on_delete=models.PROTECT, related_name="invoice", blank=True, null=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="invoices", blank=True, null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=14, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="invoices_created")

    class Meta:
        unique_together = ("tenant", "invoice_number")
        indexes = [models.Index(fields=["tenant", "invoice_number"]), models.Index(fields=["tenant", "status"])]
        ordering = ["-created_at", "-id"]


class InvoiceItem(TenantScopedModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="invoice_items")
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["tenant", "invoice"])]
