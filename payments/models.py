from django.conf import settings
from django.db import models

from core.models import TenantScopedModel


class Payment(TenantScopedModel):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        CARD = "card", "Card"
        MOBILE_MONEY = "mobile_money", "Mobile Money"
        BANK_TRANSFER = "bank_transfer", "Bank Transfer"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    sale = models.OneToOneField("sales.Sale", on_delete=models.PROTECT, related_name="payment")
    method = models.CharField(max_length=32, choices=Method.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.COMPLETED)
    reference = models.CharField(max_length=128, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments_received")
    paid_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "sale"]),
            models.Index(fields=["tenant", "method"]),
            models.Index(fields=["tenant", "status"]),
        ]
        ordering = ["-paid_at", "-id"]

