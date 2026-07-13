from decimal import Decimal, ROUND_HALF_UP

from django.db import IntegrityError, transaction
from django.utils import timezone

from catalog.models import Product
from accounts.rbac import require_tenant_role
from core.exceptions import (
    InsufficientStockError,
    PaymentMethodNotAllowedError,
    SaleAlreadyCancelledError,
    SaleNotCompletedError,
)
from inventory.models import Stock, StockMovement
from payments.models import Payment
from sales.models import Receipt, Sale, SaleItem


MONEY_QUANTUM = Decimal("0.01")


def _money(value):
    return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def calculate_sale_totals(line_items, discount, tax):
    subtotal = sum(
        (_money(item["quantity"]) * _money(item["unit_price"]) for item in line_items),
        start=Decimal("0.00"),
    )
    discount = _money(discount)
    tax = _money(tax)
    grand_total = _money(subtotal - discount + tax)
    if grand_total < 0:
        raise ValueError("Sale grand total cannot be negative.")
    return {
        "subtotal": _money(subtotal),
        "discount": discount,
        "tax": tax,
        "grand_total": grand_total,
    }


def _sale_number():
    return f"S-{timezone.now():%Y%m%d%H%M%S%f}"


def _receipt_number(sale):
    return f"R-{sale.sale_number}"


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


def _is_payment_method_allowed(tenant, payment_method):
    allowed = getattr(tenant, "allowed_payment_methods", None)
    if allowed is None:
        return True
    return payment_method in allowed


def complete_sale(tenant, cashier, cart_items, payment_method, discount=Decimal("0.00"), tax=Decimal("0.00")):
    require_tenant_role(
        cashier,
        tenant,
        (
            "owner_admin",
            "manager",
            "cashier",
        ),
        "complete sale",
    )
    with transaction.atomic():
        if not _is_payment_method_allowed(tenant, payment_method):
            raise PaymentMethodNotAllowedError(f"{payment_method} is not allowed for this tenant.")

        normalized_items = []
        for item in cart_items:
            product = item["product"]
            quantity = int(item["quantity"])
            unit_price = _money(item["unit_price"])
            normalized_items.append(
                {
                    "product": product,
                    "quantity": quantity,
                    "unit_price": unit_price,
                }
            )

        normalized_items.sort(key=lambda item: item["product"].pk)
        totals = calculate_sale_totals(normalized_items, discount, tax)

        sale = Sale.objects.create(
            tenant=tenant,
            sale_number=_sale_number(),
            status=Sale.Status.COMPLETED,
            subtotal=totals["subtotal"],
            discount_amount=totals["discount"],
            tax_amount=totals["tax"],
            grand_total=totals["grand_total"],
            cashier=cashier,
        )

        for item in normalized_items:
            product = Product.objects.select_related("category").get(pk=item["product"].pk, tenant=tenant)
            stock = _lock_or_create_stock(tenant, product)
            quantity_before = stock.quantity
            quantity_after = quantity_before - item["quantity"]
            if quantity_after < 0:
                raise InsufficientStockError(
                    f"Insufficient stock for product {product.pk}; requested {item['quantity']}, available {quantity_before}."
                )

            stock.quantity = quantity_after
            stock.last_movement_at = timezone.now()
            stock.save(update_fields=["quantity", "last_movement_at"])
            SaleItem.objects.create(
                tenant=tenant,
                sale=sale,
                product=product,
                quantity=item["quantity"],
                unit_price=item["unit_price"],
                unit_cost_snapshot=product.cost_price,
                line_subtotal=_money(item["quantity"] * item["unit_price"]),
                line_total=_money(item["quantity"] * item["unit_price"]),
            )
            StockMovement.objects.create(
                tenant=tenant,
                stock=stock,
                product=product,
                movement_type=StockMovement.MovementType.SALE_OUT,
                reference_type=StockMovement.ReferenceType.SALE,
                reference_id=sale.pk,
                quantity_delta=-item["quantity"],
                quantity_before=quantity_before,
                quantity_after=quantity_after,
                unit_cost=product.cost_price,
                note=f"Sale {sale.sale_number}",
                created_by=cashier,
            )

        Payment.objects.create(
            tenant=tenant,
            sale=sale,
            method=payment_method,
            amount=sale.grand_total,
            status=Payment.Status.COMPLETED,
            received_by=cashier,
        )

        transaction.on_commit(
            lambda: Receipt.objects.get_or_create(
                tenant=tenant,
                sale=sale,
                defaults={
                    "receipt_number": _receipt_number(sale),
                    "receipt_data": {},
                },
            )
        )

        return sale


def cancel_sale(sale_id, cancelled_by, reason):
    with transaction.atomic():
        sale = Sale.objects.select_for_update().select_related("tenant").get(pk=sale_id)
        require_tenant_role(
            cancelled_by,
            sale.tenant,
            (
                "owner_admin",
                "manager",
            ),
            "cancel sale",
        )
        if sale.status == Sale.Status.CANCELLED:
            raise SaleAlreadyCancelledError("Sale is already cancelled.")
        if sale.status != Sale.Status.COMPLETED:
            raise SaleNotCompletedError("Only completed sales can be cancelled.")

        items = list(sale.items.select_related("product").all().order_by("product_id"))
        for item in items:
            stock = _lock_or_create_stock(sale.tenant, item.product)
            quantity_before = stock.quantity
            quantity_after = quantity_before + item.quantity
            stock.quantity = quantity_after
            stock.last_movement_at = timezone.now()
            stock.save(update_fields=["quantity", "last_movement_at"])
            StockMovement.objects.create(
                tenant=sale.tenant,
                stock=stock,
                product=item.product,
                movement_type=StockMovement.MovementType.SALE_REVERSAL,
                reference_type=StockMovement.ReferenceType.SALE,
                reference_id=sale.pk,
                quantity_delta=item.quantity,
                quantity_before=quantity_before,
                quantity_after=quantity_after,
                unit_cost=item.unit_cost_snapshot,
                note=f"Sale cancellation {sale.sale_number}: {reason}",
                created_by=cancelled_by,
            )

        sale.status = Sale.Status.CANCELLED
        sale.cancelled_by = cancelled_by
        sale.cancelled_at = timezone.now()
        sale.cancel_reason = reason
        sale.save(update_fields=["status", "cancelled_by", "cancelled_at", "cancel_reason", "updated_at"])
        return sale
