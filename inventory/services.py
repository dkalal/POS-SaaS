from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.rbac import require_tenant_role
from audit.models import AuditEvent
from audit.services import log_audit_event, snapshot_stock_adjustment
from catalog.models import Product
from core.exceptions import (
    InsufficientStockError,
    StockAdjustmentAlreadyPostedError,
    StockAdjustmentNotDraftError,
    StockAdjustmentNotPostedError,
)
from inventory.models import Stock, StockAdjustment, StockAdjustmentItem, StockMovement


def _money(value):
    return Decimal(value).quantize(Decimal("0.00"))


def _generate_adjustment_number():
    return f"SA-{timezone.now():%Y%m%d%H%M%S%f}"


def _lock_or_create_stock(tenant, product):
    try:
        stock, created = Stock.objects.get_or_create(
            tenant=tenant,
            product=product,
            defaults={"quantity": 0, "cost_value": Decimal("0.00")},
        )
    except IntegrityError:
        stock = Stock.objects.get(tenant=tenant, product=product)
        created = False

    if not created:
        stock = Stock.objects.select_for_update().get(pk=stock.pk)
    return stock


def _current_quantity(tenant, product):
    stock = Stock.objects.filter(tenant=tenant, product=product).first()
    return stock.quantity if stock is not None else 0


def _normalize_items(tenant, items, *, enforce_available=False):
    normalized = []
    seen = set()
    for item in items:
        product = item["product"]
        if product.pk in seen:
            raise ValueError("Each product can appear only once in a stock adjustment.")
        seen.add(product.pk)
        quantity = int(item["quantity"])
        if quantity <= 0:
            raise ValueError("Each adjustment line needs a positive quantity.")
        direction = item["direction"]
        delta = quantity if direction == "increase" else -quantity
        note = (item.get("note") or "").strip()
        before = _current_quantity(tenant, product)
        after = before + delta
        if enforce_available and after < 0:
            raise InsufficientStockError(
                f"Cannot apply adjustment for {product.name}; stock would go negative."
            )
        normalized.append(
            {
                "product": product,
                "quantity": quantity,
                "direction": direction,
                "quantity_delta": delta,
                "quantity_before": before,
                "quantity_after": after,
                "note": note,
            }
        )
    if not normalized:
        raise ValueError("Add at least one stock adjustment line.")
    return normalized


def create_draft_adjustment(tenant, reason, items, created_by, notes=""):
    require_tenant_role(
        created_by,
        tenant,
        (
            "owner_admin",
            "manager",
        ),
        "create stock adjustment",
    )
    normalized_items = _normalize_items(tenant, items)
    with transaction.atomic():
        adjustment = StockAdjustment.objects.create(
            tenant=tenant,
            adjustment_number=_generate_adjustment_number(),
            reason=reason,
            notes=(notes or "").strip(),
            status=StockAdjustment.Status.DRAFT,
            created_by=created_by,
        )
        for item in normalized_items:
            StockAdjustmentItem.objects.create(
                tenant=tenant,
                adjustment=adjustment,
                product=item["product"],
                quantity_before=item["quantity_before"],
                quantity_after=item["quantity_after"],
                quantity_delta=item["quantity_delta"],
                note=item["note"],
            )
        log_audit_event(
            tenant=tenant,
            actor=created_by,
            action=AuditEvent.Action.STOCK_ADJUSTMENT_CREATED,
            target=adjustment,
            after_data=snapshot_stock_adjustment(adjustment),
        )
        return adjustment


def update_draft_adjustment(adjustment_id, tenant, reason, items, updated_by, notes=""):
    require_tenant_role(
        updated_by,
        tenant,
        (
            "owner_admin",
            "manager",
        ),
        "edit stock adjustment",
    )
    normalized_items = _normalize_items(tenant, items)
    with transaction.atomic():
        adjustment = StockAdjustment.objects.select_for_update().get(pk=adjustment_id, tenant=tenant)
        if adjustment.status != StockAdjustment.Status.DRAFT:
            raise StockAdjustmentNotDraftError("Only draft adjustments can be edited.")
        before_data = snapshot_stock_adjustment(adjustment)
        adjustment.reason = reason
        adjustment.notes = (notes or "").strip()
        adjustment.save(update_fields=["reason", "notes", "updated_at"])
        adjustment.items.all().delete()
        for item in normalized_items:
            StockAdjustmentItem.objects.create(
                tenant=tenant,
                adjustment=adjustment,
                product=item["product"],
                quantity_before=item["quantity_before"],
                quantity_after=item["quantity_after"],
                quantity_delta=item["quantity_delta"],
                note=item["note"],
            )
        log_audit_event(
            tenant=tenant,
            actor=updated_by,
            action=AuditEvent.Action.STOCK_ADJUSTMENT_UPDATED,
            target=adjustment,
            before_data=before_data,
            after_data=snapshot_stock_adjustment(adjustment),
        )
        return adjustment


def post_adjustment(adjustment_id, posted_by):
    with transaction.atomic():
        adjustment = StockAdjustment.objects.select_for_update().select_related("tenant").get(pk=adjustment_id)
        require_tenant_role(
            posted_by,
            adjustment.tenant,
            (
                "owner_admin",
            ),
            "post stock adjustment",
        )
        if adjustment.status == StockAdjustment.Status.POSTED:
            raise StockAdjustmentAlreadyPostedError("Adjustment has already been posted.")
        if adjustment.status == StockAdjustment.Status.CANCELLED:
            raise StockAdjustmentNotPostedError("Cancelled adjustments cannot be posted.")

        before_data = snapshot_stock_adjustment(adjustment)
        items = list(adjustment.items.select_related("product").all().order_by("id"))
        for item in items:
            stock = _lock_or_create_stock(adjustment.tenant, item.product)
            quantity_before = stock.quantity
            quantity_after = quantity_before + item.quantity_delta
            if quantity_after < 0:
                raise InsufficientStockError(
                    f"Cannot post adjustment {adjustment.adjustment_number}; product {item.product.name} would go negative."
                )
            stock.quantity = quantity_after
            stock.last_movement_at = timezone.now()
            stock.save(update_fields=["quantity", "last_movement_at"])
            item.quantity_before = quantity_before
            item.quantity_after = quantity_after
            item.save(update_fields=["quantity_before", "quantity_after"])
            StockMovement.objects.create(
                tenant=adjustment.tenant,
                stock=stock,
                product=item.product,
                movement_type=(
                    StockMovement.MovementType.ADJUSTMENT_IN
                    if item.quantity_delta > 0
                    else StockMovement.MovementType.ADJUSTMENT_OUT
                ),
                reference_type=StockMovement.ReferenceType.STOCK_ADJUSTMENT,
                reference_id=adjustment.pk,
                quantity_delta=item.quantity_delta,
                quantity_before=quantity_before,
                quantity_after=quantity_after,
                unit_cost=None,
                note=item.note or adjustment.reason,
                created_by=posted_by,
            )

        adjustment.status = StockAdjustment.Status.POSTED
        adjustment.posted_by = posted_by
        adjustment.posted_at = timezone.now()
        adjustment.save(update_fields=["status", "posted_by", "posted_at", "updated_at"])
        log_audit_event(
            tenant=adjustment.tenant,
            actor=posted_by,
            action=AuditEvent.Action.STOCK_ADJUSTMENT_POSTED,
            target=adjustment,
            before_data=before_data,
            after_data=snapshot_stock_adjustment(adjustment),
        )
        return adjustment


def cancel_adjustment(adjustment_id, cancelled_by, reason=""):
    with transaction.atomic():
        adjustment = StockAdjustment.objects.select_for_update().select_related("tenant").get(pk=adjustment_id)
        require_tenant_role(
            cancelled_by,
            adjustment.tenant,
            (
                "owner_admin",
            ),
            "cancel stock adjustment",
        )
        if adjustment.status == StockAdjustment.Status.CANCELLED:
            raise StockAdjustmentNotPostedError("Adjustment has already been cancelled.")
        before_data = snapshot_stock_adjustment(adjustment)
        if adjustment.status == StockAdjustment.Status.POSTED:
            items = list(adjustment.items.select_related("product").all().order_by("id"))
            for item in items:
                stock = _lock_or_create_stock(adjustment.tenant, item.product)
                quantity_before = stock.quantity
                quantity_delta = -item.quantity_delta
                quantity_after = quantity_before + quantity_delta
                if quantity_after < 0:
                    raise InsufficientStockError(
                        f"Cannot cancel adjustment {adjustment.adjustment_number}; product {item.product.name} would go negative."
                    )
                stock.quantity = quantity_after
                stock.last_movement_at = timezone.now()
                stock.save(update_fields=["quantity", "last_movement_at"])
                StockMovement.objects.create(
                    tenant=adjustment.tenant,
                    stock=stock,
                    product=item.product,
                    movement_type=(
                        StockMovement.MovementType.ADJUSTMENT_IN
                        if quantity_delta > 0
                        else StockMovement.MovementType.ADJUSTMENT_OUT
                    ),
                    reference_type=StockMovement.ReferenceType.STOCK_ADJUSTMENT,
                    reference_id=adjustment.pk,
                    quantity_delta=quantity_delta,
                    quantity_before=quantity_before,
                    quantity_after=quantity_after,
                    unit_cost=None,
                    note=reason or adjustment.reason,
                    created_by=cancelled_by,
                )

        adjustment.status = StockAdjustment.Status.CANCELLED
        adjustment.cancelled_by = cancelled_by
        adjustment.cancelled_at = timezone.now()
        adjustment.cancel_reason = (reason or "").strip()
        adjustment.save(
            update_fields=["status", "cancelled_by", "cancelled_at", "cancel_reason", "updated_at"]
        )
        log_audit_event(
            tenant=adjustment.tenant,
            actor=cancelled_by,
            action=AuditEvent.Action.STOCK_ADJUSTMENT_CANCELLED,
            target=adjustment,
            before_data=before_data,
            after_data=snapshot_stock_adjustment(adjustment),
            metadata={"cancel_reason": adjustment.cancel_reason},
        )
        return adjustment
