from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from catalog.models import Product
from core.exceptions import (
    InsufficientStockError,
    InvalidPurchaseInputError,
    PurchaseAlreadyReceivedError,
    PurchaseNotDraftError,
    PurchaseNotReceivedError,
)
from audit.models import AuditEvent
from audit.services import log_audit_event, snapshot_purchase
from accounts.rbac import require_tenant_role
from inventory.models import Stock, StockMovement
from purchasing.models import Purchase, PurchaseItem
from suppliers.models import Supplier


def _money(value):
    return Decimal(value).quantize(Decimal("0.01"))


def _generate_purchase_number():
    return f"P-{timezone.now():%Y%m%d%H%M%S%f}"


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


def _validate_purchase_inputs(tenant, supplier, items):
    if supplier is None or supplier.tenant_id != tenant.id:
        raise InvalidPurchaseInputError("Choose a supplier from the active workspace.")
    if not supplier.is_active:
        raise InvalidPurchaseInputError("Archived suppliers cannot be used for new purchases.")
    if not items:
        raise InvalidPurchaseInputError("Add at least one physical product to the purchase.")

    for item in items:
        product = item.get("product")
        if product is None or product.tenant_id != tenant.id:
            raise InvalidPurchaseInputError("Purchase products must belong to the active workspace.")
        if not product.is_active or not product.track_inventory:
            raise InvalidPurchaseInputError("Only active physical products can be purchased into stock.")
        if int(item.get("quantity") or 0) <= 0:
            raise InvalidPurchaseInputError("Purchase quantities must be greater than zero.")
        if _money(item.get("unit_cost") or 0) < Decimal("0.00"):
            raise InvalidPurchaseInputError("Purchase unit costs cannot be negative.")


def create_draft_purchase(tenant, supplier, items, created_by, order_date=None, expected_date=None, notes=""):
    require_tenant_role(
        created_by,
        tenant,
        (
            "owner_admin",
            "manager",
        ),
        "create draft purchase",
    )
    with transaction.atomic():
        supplier = Supplier.objects.select_for_update().get(pk=supplier.pk, tenant=tenant)
        _validate_purchase_inputs(tenant, supplier, items)
        purchase = Purchase.objects.create(
            tenant=tenant,
            supplier=supplier,
            purchase_number=_generate_purchase_number(),
            order_date=order_date or timezone.localdate(),
            expected_date=expected_date,
            status=Purchase.Status.DRAFT,
            notes=(notes or "").strip(),
            created_by=created_by,
        )

        for item in items:
            product = item["product"]
            quantity = int(item["quantity"])
            unit_cost = _money(item["unit_cost"])
            PurchaseItem.objects.create(
                tenant=tenant,
                purchase=purchase,
                product=product,
                quantity=quantity,
                unit_cost=unit_cost,
                line_total=_money(quantity * unit_cost),
            )

        log_audit_event(
            tenant=tenant,
            actor=created_by,
            action=AuditEvent.Action.PURCHASE_CREATED,
            target=purchase,
            after_data=snapshot_purchase(purchase),
        )
        return purchase


def update_draft_purchase(purchase_id, tenant, supplier, items, updated_by, order_date=None, expected_date=None, notes=""):
    with transaction.atomic():
        purchase = Purchase.objects.select_for_update().select_related("tenant").get(pk=purchase_id, tenant=tenant)
        require_tenant_role(
            updated_by,
            tenant,
            (
                "owner_admin",
                "manager",
            ),
            "edit draft purchase",
        )
        if purchase.status != Purchase.Status.DRAFT:
            raise PurchaseNotDraftError("Only draft purchases can be edited.")

        supplier = Supplier.objects.select_for_update().get(pk=supplier.pk, tenant=tenant)
        _validate_purchase_inputs(tenant, supplier, items)
        before_data = snapshot_purchase(purchase)
        purchase.supplier = supplier
        if order_date is not None:
            purchase.order_date = order_date
        purchase.expected_date = expected_date
        purchase.notes = (notes or "").strip()
        purchase.save(update_fields=["supplier", "order_date", "expected_date", "notes", "updated_at"])

        purchase.items.all().delete()
        for item in items:
            product = item["product"]
            quantity = int(item["quantity"])
            unit_cost = _money(item["unit_cost"])
            PurchaseItem.objects.create(
                tenant=tenant,
                purchase=purchase,
                product=product,
                quantity=quantity,
                unit_cost=unit_cost,
                line_total=_money(quantity * unit_cost),
            )
        log_audit_event(
            tenant=tenant,
            actor=updated_by,
            action=AuditEvent.Action.PURCHASE_UPDATED,
            target=purchase,
            before_data=before_data,
            after_data=snapshot_purchase(purchase),
        )
        return purchase


def duplicate_purchase(purchase_id, duplicated_by):
    source = Purchase.objects.select_related("tenant", "supplier").get(pk=purchase_id)
    require_tenant_role(
        duplicated_by,
        source.tenant,
        (
            "owner_admin",
            "manager",
        ),
        "duplicate purchase",
    )
    items = [
        {
            "product": item.product,
            "quantity": item.quantity,
            "unit_cost": item.unit_cost,
        }
        for item in source.items.select_related("product").all().order_by("id")
    ]
    duplicate = create_draft_purchase(
        tenant=source.tenant,
        supplier=source.supplier,
        items=items,
        created_by=duplicated_by,
        order_date=source.order_date,
        expected_date=source.expected_date,
        notes=source.notes,
    )
    log_audit_event(
        tenant=source.tenant,
        actor=duplicated_by,
        action=AuditEvent.Action.PURCHASE_DUPLICATED,
        target=duplicate,
        before_data=snapshot_purchase(source),
        after_data=snapshot_purchase(duplicate),
        metadata={"source_purchase_id": source.pk},
    )
    return duplicate


def receive_purchase(purchase_id, received_by):
    with transaction.atomic():
        purchase = (
            Purchase.objects.select_for_update()
            .select_related("tenant", "supplier")
            .get(pk=purchase_id)
        )
        require_tenant_role(
            received_by,
            purchase.tenant,
            (
                "owner_admin",
                "manager",
            ),
            "receive purchase",
        )
        if purchase.status == Purchase.Status.RECEIVED:
            raise PurchaseAlreadyReceivedError("Purchase has already been received.")
        if purchase.status == Purchase.Status.CANCELLED:
            raise PurchaseNotReceivedError("Cancelled purchases cannot be received.")

        before_data = snapshot_purchase(purchase)
        items = list(
            purchase.items.select_related("product").all().order_by("product_id")
        )
        if not items:
            raise InvalidPurchaseInputError("A purchase without items cannot be received.")
        for item in items:
            if item.product.tenant_id != purchase.tenant_id or not item.product.track_inventory:
                raise InvalidPurchaseInputError("Only physical products from this workspace can be received into stock.")
            stock = _lock_or_create_stock(purchase.tenant, item.product)
            quantity_before = stock.quantity
            quantity_after = quantity_before + item.quantity
            stock.quantity = quantity_after
            stock.last_movement_at = timezone.now()
            stock.save(update_fields=["quantity", "last_movement_at"])
            StockMovement.objects.create(
                tenant=purchase.tenant,
                stock=stock,
                product=item.product,
                movement_type=StockMovement.MovementType.PURCHASE_IN,
                reference_type=StockMovement.ReferenceType.PURCHASE,
                reference_id=purchase.pk,
                quantity_delta=item.quantity,
                quantity_before=quantity_before,
                quantity_after=quantity_after,
                unit_cost=item.unit_cost,
                note=f"Purchase receipt {purchase.purchase_number}",
                created_by=received_by,
            )

        purchase.status = Purchase.Status.RECEIVED
        purchase.received_by = received_by
        purchase.received_date = timezone.now()
        purchase.save(
            update_fields=["status", "received_by", "received_date", "updated_at"]
        )
        log_audit_event(
            tenant=purchase.tenant,
            actor=received_by,
            action=AuditEvent.Action.PURCHASE_RECEIVED,
            target=purchase,
            before_data=before_data,
            after_data=snapshot_purchase(purchase),
        )
        return purchase


def cancel_received_purchase(purchase_id, cancelled_by, reason=""):
    with transaction.atomic():
        purchase = (
            Purchase.objects.select_for_update()
            .select_related("tenant", "supplier")
            .get(pk=purchase_id)
        )
        require_tenant_role(
            cancelled_by,
            purchase.tenant,
            (
                "owner_admin",
                "manager",
            ),
            "cancel received purchase",
        )
        if purchase.status != Purchase.Status.RECEIVED:
            raise PurchaseNotReceivedError("Only received purchases can be cancelled.")

        before_data = snapshot_purchase(purchase)
        items = list(
            purchase.items.select_related("product").all().order_by("product_id")
        )
        for item in items:
            stock = _lock_or_create_stock(purchase.tenant, item.product)
            quantity_before = stock.quantity
            quantity_after = quantity_before - item.quantity
            if quantity_after < 0:
                raise InsufficientStockError(
                    f"Cannot cancel purchase {purchase.pk}; product {item.product_id} would go negative."
                )
            stock.quantity = quantity_after
            stock.last_movement_at = timezone.now()
            stock.save(update_fields=["quantity", "last_movement_at"])
            StockMovement.objects.create(
                tenant=purchase.tenant,
                stock=stock,
                product=item.product,
                movement_type=StockMovement.MovementType.PURCHASE_REVERSAL,
                reference_type=StockMovement.ReferenceType.PURCHASE,
                reference_id=purchase.pk,
                quantity_delta=-item.quantity,
                quantity_before=quantity_before,
                quantity_after=quantity_after,
                unit_cost=item.unit_cost,
                note=f"Purchase cancellation {purchase.purchase_number}",
                created_by=cancelled_by,
            )

        purchase.status = Purchase.Status.CANCELLED
        purchase.cancelled_by = cancelled_by
        purchase.cancelled_date = timezone.now()
        purchase.cancelled_reason = (reason or "").strip()
        purchase.save(
            update_fields=["status", "cancelled_by", "cancelled_date", "cancelled_reason", "updated_at"]
        )
        log_audit_event(
            tenant=purchase.tenant,
            actor=cancelled_by,
            action=AuditEvent.Action.PURCHASE_CANCELLED,
            target=purchase,
            before_data=before_data,
            after_data=snapshot_purchase(purchase),
            metadata={"cancel_reason": purchase.cancelled_reason},
        )
        return purchase


def update_cost_price_from_purchase(purchase):
    """
    Explicit policy hook. Keep it separate so a tenant setting can control it later.
    """
    for item in purchase.items.select_related("product").all():
        product = item.product
        product.cost_price = item.unit_cost
        product.save(update_fields=["cost_price"])
