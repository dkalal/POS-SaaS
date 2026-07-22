from decimal import Decimal, ROUND_HALF_UP

from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import log_audit_event
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
from sales.models import Customer, Invoice, InvoiceItem, Quotation, QuotationItem, Receipt, Sale, SaleItem


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


def _invoice_number(sale):
    return f"I-{sale.sale_number}"


def _quotation_number():
    return f"Q-{timezone.now():%Y%m%d%H%M%S%f}"


def _tenant_customer(tenant, customer):
    if customer is None:
        return None
    return Customer.objects.get(pk=customer.pk, tenant=tenant)


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


def complete_sale(
    tenant,
    cashier,
    cart_items,
    payment_method,
    discount=Decimal("0.00"),
    tax=Decimal("0.00"),
    reference="",
    checkout_key="",
    invoice=None,
    customer=None,
):
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
        if checkout_key:
            existing = Sale.objects.filter(tenant=tenant, checkout_key=checkout_key).first()
            if existing is not None:
                return existing

        # A draft invoice can be paid only once.  Re-read it under a row lock so
        # two browser retries cannot both proceed from a stale draft instance.
        if invoice is not None:
            invoice = Invoice.objects.select_for_update().get(pk=invoice.pk, tenant=tenant)
            if invoice.sale_id:
                return invoice.sale
            if invoice.status != Invoice.Status.DRAFT:
                raise ValueError("Only an unconfirmed invoice in this business can be paid.")
            customer = invoice.customer
        customer = _tenant_customer(tenant, customer)
        if payment_method not in Payment.Method.values:
            raise PaymentMethodNotAllowedError("Select a valid payment method.")
        if not _is_payment_method_allowed(tenant, payment_method):
            raise PaymentMethodNotAllowedError(f"{payment_method} is not allowed for this tenant.")

        normalized_items = []
        for item in cart_items:
            product = Product.objects.get(pk=item["product"].pk, tenant=tenant, is_active=True)
            quantity = int(item["quantity"])
            if quantity < 1:
                raise ValueError("Sale quantities must be at least one.")
            unit_price = _money(item["unit_price"])
            normalized_items.append(
                {
                    "product": product,
                    "quantity": quantity,
                    "unit_price": unit_price,
                }
            )

        if not normalized_items:
            raise ValueError("A sale must contain at least one item.")

        normalized_items.sort(key=lambda item: item["product"].pk)
        totals = calculate_sale_totals(normalized_items, discount, tax)

        sale = Sale.objects.create(
            tenant=tenant,
            sale_number=_sale_number(),
            checkout_key=checkout_key,
            status=Sale.Status.COMPLETED,
            subtotal=totals["subtotal"],
            discount_amount=totals["discount"],
            tax_amount=totals["tax"],
            grand_total=totals["grand_total"],
            customer=customer,
            cashier=cashier,
        )

        created_items = []
        for item in normalized_items:
            product = Product.objects.select_related("category").get(pk=item["product"].pk, tenant=tenant)
            created_items.append(SaleItem.objects.create(
                tenant=tenant,
                sale=sale,
                product=product,
                quantity=item["quantity"],
                unit_price=item["unit_price"],
                unit_cost_snapshot=product.cost_price,
                line_subtotal=_money(item["quantity"] * item["unit_price"]),
                line_total=_money(item["quantity"] * item["unit_price"]),
            ))
            if product.track_inventory:
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
            reference=reference or "",
            received_by=cashier,
        )

        if invoice is None:
            invoice = Invoice.objects.create(
                tenant=tenant, sale=sale, status=Invoice.Status.PAID,
                invoice_number=_invoice_number(sale), subtotal=sale.subtotal,
                discount_amount=sale.discount_amount, tax_amount=sale.tax_amount,
                grand_total=sale.grand_total, created_by=cashier,
                customer=customer,
            )
            InvoiceItem.objects.bulk_create([
                InvoiceItem(tenant=tenant, invoice=invoice, product=item.product, quantity=item.quantity,
                            unit_price=item.unit_price, line_total=item.line_total)
                for item in created_items
            ])
        else:
            invoice.sale = sale
            invoice.status = Invoice.Status.PAID
            invoice.save(update_fields=["sale", "status", "updated_at"])
        receipt = Receipt.objects.create(
            tenant=tenant,
            sale=sale,
            receipt_number=_receipt_number(sale),
            receipt_data={"invoice_number": invoice.invoice_number, "payment_method": payment_method},
        )

        return sale


def save_quotation(*, tenant, actor, customer, expires_at, discount, tax, line_items, quotation=None):
    """Create or update a tenant quotation. Offers never reserve or deduct stock."""
    require_tenant_role(actor, tenant, ("owner_admin", "manager"), "manage quotation")
    customer = _tenant_customer(tenant, customer)
    normalized = []
    for line in line_items:
        product = Product.objects.get(pk=line["product"].pk, tenant=tenant, is_active=True)
        quantity = int(line["quantity"])
        if quantity < 1:
            raise ValueError("Quotation quantities must be at least one.")
        normalized.append({"product": product, "quantity": quantity, "unit_price": _money(product.sale_price)})
    if not normalized:
        raise ValueError("A quotation must contain at least one item.")
    totals = calculate_sale_totals(normalized, discount, tax)

    with transaction.atomic():
        created = quotation is None
        if quotation is None:
            quotation = Quotation(tenant=tenant, quotation_number=_quotation_number(), created_by=actor)
        else:
            quotation = Quotation.objects.select_for_update().get(pk=quotation.pk, tenant=tenant)
            if quotation.status != Quotation.Status.DRAFT or quotation.converted_invoice_id:
                raise ValueError("Only an unconverted draft quotation can be edited.")
        before_data = {} if created else {
            "customer_id": quotation.customer_id,
            "expires_at": quotation.expires_at.isoformat() if quotation.expires_at else None,
            "grand_total": str(quotation.grand_total),
        }
        quotation.customer = customer
        quotation.expires_at = expires_at
        quotation.subtotal = totals["subtotal"]
        quotation.discount_amount = totals["discount"]
        quotation.tax_amount = totals["tax"]
        quotation.grand_total = totals["grand_total"]
        quotation.save()
        if not created:
            quotation.items.all().delete()
        QuotationItem.objects.bulk_create([
            QuotationItem(
                tenant=tenant,
                quotation=quotation,
                product=line["product"],
                quantity=line["quantity"],
                unit_price=line["unit_price"],
                line_total=_money(line["quantity"] * line["unit_price"]),
            )
            for line in normalized
        ])
        log_audit_event(
            tenant=tenant,
            actor=actor,
            action=AuditEvent.Action.QUOTATION_CREATED if created else AuditEvent.Action.QUOTATION_UPDATED,
            target=quotation,
            before_data=before_data,
            after_data={"quotation_number": quotation.quotation_number, "status": quotation.status, "grand_total": str(quotation.grand_total)},
        )
        return quotation


QUOTATION_STATUS_TRANSITIONS = {
    Quotation.Status.DRAFT: {Quotation.Status.SENT, Quotation.Status.EXPIRED},
    Quotation.Status.SENT: {Quotation.Status.ACCEPTED, Quotation.Status.REJECTED, Quotation.Status.EXPIRED},
    Quotation.Status.ACCEPTED: {Quotation.Status.EXPIRED},
}


def change_quotation_status(*, quotation, actor, status):
    require_tenant_role(actor, quotation.tenant, ("owner_admin", "manager"), "change quotation status")
    with transaction.atomic():
        quotation = Quotation.objects.select_for_update().get(pk=quotation.pk, tenant=quotation.tenant)
        if status not in QUOTATION_STATUS_TRANSITIONS.get(quotation.status, set()):
            raise ValueError("That quotation status change is not allowed.")
        before = quotation.status
        quotation.status = status
        quotation.save(update_fields=["status", "updated_at"])
        log_audit_event(
            tenant=quotation.tenant, actor=actor, action=AuditEvent.Action.QUOTATION_STATUS_CHANGED,
            target=quotation, before_data={"status": before}, after_data={"status": status},
        )
        return quotation


def convert_quotation_to_invoice(*, quotation, actor):
    """Atomically create one draft invoice from an offer without touching stock."""
    require_tenant_role(actor, quotation.tenant, ("owner_admin", "manager"), "convert quotation")
    with transaction.atomic():
        quotation = Quotation.objects.select_for_update().prefetch_related("items__product").get(
            pk=quotation.pk, tenant=quotation.tenant
        )
        if quotation.converted_invoice_id:
            return quotation.converted_invoice
        if quotation.expires_at and quotation.expires_at < timezone.localdate():
            raise ValueError("This quotation has expired and cannot be converted.")
        if quotation.status not in (Quotation.Status.DRAFT, Quotation.Status.SENT, Quotation.Status.ACCEPTED):
            raise ValueError("This quotation cannot be converted.")
        invoice = Invoice.objects.create(
            tenant=quotation.tenant,
            invoice_number=f"I-{quotation.quotation_number}",
            customer=quotation.customer,
            subtotal=quotation.subtotal,
            discount_amount=quotation.discount_amount,
            tax_amount=quotation.tax_amount,
            grand_total=quotation.grand_total,
            created_by=actor,
        )
        InvoiceItem.objects.bulk_create([
            InvoiceItem(tenant=quotation.tenant, invoice=invoice, product=item.product,
                        quantity=item.quantity, unit_price=item.unit_price, line_total=item.line_total)
            for item in quotation.items.all()
        ])
        quotation.status = Quotation.Status.CONVERTED
        quotation.converted_invoice = invoice
        quotation.save(update_fields=["status", "converted_invoice", "updated_at"])
        log_audit_event(
            tenant=quotation.tenant, actor=actor, action=AuditEvent.Action.QUOTATION_CONVERTED,
            target=quotation, after_data={"status": quotation.status, "invoice_number": invoice.invoice_number},
        )
        return invoice


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
            if not StockMovement.objects.filter(
                tenant=sale.tenant,
                product=item.product,
                movement_type=StockMovement.MovementType.SALE_OUT,
                reference_type=StockMovement.ReferenceType.SALE,
                reference_id=sale.pk,
            ).exists():
                continue
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
