from contextlib import contextmanager
from decimal import Decimal
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.test.signals import template_rendered
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from accounts.models import TenantMembership
from accounts.rbac import active_membership_for, tenant_role_required
from audit.models import AuditEvent
from audit.services import log_audit_event
from catalog.models import Category, Product
from core.exceptions import DomainError, InsufficientStockError, PaymentMethodNotAllowedError
from payments.models import Payment
from sales.forms import (
    QuotationForm,
    QuotationLineFormSet,
    RegisterCartAdjustForm,
    RegisterCheckoutForm,
    RegisterPricingForm,
    RegisterSearchForm,
)
from sales.models import Customer, Invoice, Quotation, Receipt, Sale
from inventory.models import StockMovement
from sales.services import (
    QUOTATION_STATUS_TRANSITIONS,
    calculate_sale_totals,
    change_quotation_status,
    complete_sale,
    convert_quotation_to_invoice,
    save_quotation,
)
from core.money import format_money


@contextmanager
def _suppress_template_render_signal():
    receivers = list(template_rendered.receivers)
    cache = template_rendered.sender_receivers_cache.copy()
    template_rendered.receivers = []
    template_rendered.sender_receivers_cache.clear()
    try:
        yield
    finally:
        template_rendered.receivers = receivers
        template_rendered.sender_receivers_cache = cache


def _html_response(request, template, context, status=200):
    with _suppress_template_render_signal():
        html = render_to_string(template, context, request=request)
    return HttpResponse(html, status=status)


def _tenant_or_redirect(request):
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        return None, redirect("dashboard")
    return tenant, None


def _cart_session_key(tenant):
    return f"sales_register_cart_{tenant.id}"


def _register_session_key(tenant):
    return f"sales_register_meta_{tenant.id}"


def _register_redirect(request, *, reset_filters=False):
    """Return to the Register without losing the cashier's active catalog context."""
    url = reverse("sales:register")
    if reset_filters:
        return redirect(url)
    query = request.GET.copy()
    for key in list(query.keys()):
        if key not in {"q", "category", "page"}:
            query.pop(key)
    encoded_query = query.urlencode()
    return redirect(f"{url}?{encoded_query}" if encoded_query else url)


def _read_cart(request, tenant):
    session_key = _cart_session_key(tenant)
    raw_cart = request.session.get(session_key, {})
    cart = []
    if isinstance(raw_cart, dict):
        for product_id, payload in raw_cart.items():
            try:
                cart.append(
                    {
                        "product_id": int(product_id),
                        "quantity": int(payload.get("quantity", 0)),
                        "unit_price": Decimal(str(payload.get("unit_price", "0.00"))),
                    }
                )
            except (TypeError, ValueError, ArithmeticError):
                continue
    cart.sort(key=lambda item: item["product_id"])
    return cart


def _write_cart(request, tenant, cart):
    session_key = _cart_session_key(tenant)
    request.session[session_key] = {
        str(item["product_id"]): {
            "quantity": int(item["quantity"]),
            "unit_price": str(item["unit_price"]),
        }
        for item in cart
    }
    request.session.modified = True


def _read_register_meta(request, tenant):
    meta = request.session.get(_register_session_key(tenant), {})
    return {
        "discount": Decimal(str(meta.get("discount", "0.00"))),
        "tax": Decimal(str(meta.get("tax", "0.00"))),
        "payment_method": meta.get("payment_method", Payment.Method.CASH),
        "reference": meta.get("reference", ""),
    }


def _write_register_meta(request, tenant, *, discount, tax, payment_method, reference):
    request.session[_register_session_key(tenant)] = {
        "discount": str(discount),
        "tax": str(tax),
        "payment_method": payment_method,
        "reference": reference or "",
    }
    request.session.modified = True


def _clear_register_state(request, tenant):
    request.session.pop(_cart_session_key(tenant), None)
    request.session.pop(_register_session_key(tenant), None)
    request.session.modified = True


def _checkout_key(request, tenant):
    key = f"sales_register_checkout_key_{tenant.id}"
    value = request.session.get(key)
    if not value:
        value = uuid4().hex
        request.session[key] = value
        request.session.modified = True
    return value


def _product_stock_quantity(product):
    if not product.track_inventory:
        return None
    try:
        return product.stock.quantity
    except ObjectDoesNotExist:
        return 0


def _cart_products(tenant, cart):
    if not cart:
        return []
    products = Product.objects.select_related("category", "stock").filter(
        tenant=tenant,
        is_active=True,
        pk__in=[item["product_id"] for item in cart],
    )
    product_map = {product.pk: product for product in products}
    lines = []
    for item in cart:
        product = product_map.get(item["product_id"])
        if product is None:
            continue
        lines.append(
            {
                "product": product,
                "quantity": item["quantity"],
                "unit_price": item["unit_price"],
                "line_total": (Decimal(item["quantity"]) * item["unit_price"]).quantize(Decimal("0.01")),
                "stock_quantity": _product_stock_quantity(product),
            }
        )
    return lines


def _register_context(request, tenant, *, search_form=None, pricing_form=None, checkout_form=None):
    cart = _read_cart(request, tenant)
    meta = _read_register_meta(request, tenant)
    cart_lines = _cart_products(tenant, cart)
    totals = calculate_sale_totals(
        [{"quantity": line["quantity"], "unit_price": line["unit_price"]} for line in cart_lines],
        discount=meta["discount"],
        tax=meta["tax"],
    ) if cart_lines else {
        "subtotal": Decimal("0.00"),
        "discount": meta["discount"],
        "tax": meta["tax"],
        "grand_total": Decimal("0.00"),
    }
    for line in cart_lines:
        line["unit_price"] = format_money(line["unit_price"], tenant.currency)
        line["line_total"] = format_money(line["line_total"], tenant.currency)
    totals = {key: format_money(value, tenant.currency) for key, value in totals.items()}
    q = (search_form.cleaned_data["q"] if search_form and search_form.is_valid() else (request.GET.get("q") or "")).strip()
    categories = Category.objects.filter(
        tenant=tenant,
        is_active=True,
        products__tenant=tenant,
        products__is_active=True,
    ).distinct().order_by("sort_order", "name")
    selected_category = request.GET.get("category", "").strip()
    category_ids = {str(category.pk) for category in categories}
    if selected_category not in category_ids:
        selected_category = ""

    products = Product.objects.select_related("category", "stock").filter(tenant=tenant, is_active=True)
    has_catalog_products = products.exists()
    if selected_category:
        products = products.filter(category_id=selected_category)
    if q:
        products = products.filter(
            (
                Q(name__icontains=q)
                | Q(sku__icontains=q)
                | Q(barcode__icontains=q)
                | Q(category__name__icontains=q)
            )
        ).annotate(
            search_priority=Case(
                When(sku__iexact=q, then=Value(0)),
                When(sku__istartswith=q, then=Value(1)),
                When(sku__icontains=q, then=Value(2)),
                When(name__iexact=q, then=Value(3)),
                When(name__istartswith=q, then=Value(4)),
                When(name__icontains=q, then=Value(5)),
                When(barcode__iexact=q, then=Value(6)),
                When(barcode__istartswith=q, then=Value(7)),
                When(barcode__icontains=q, then=Value(8)),
                default=Value(9),
                output_field=IntegerField(),
            )
        ).order_by("search_priority", "name", "sku", "pk")
    else:
        products = products.order_by("name", "sku", "pk")
    cart_quantities = {line["product_id"]: line["quantity"] for line in cart}
    product_cards = []
    product_page = Paginator(products, 24).get_page(request.GET.get("page"))
    for product in product_page:
        stock_quantity = _product_stock_quantity(product)
        product_cards.append(
            {
                "product": product,
                "stock_quantity": stock_quantity,
                "category_name": product.category.name if product.category_id else "Uncategorized",
                "quantity_in_cart": cart_quantities.get(product.pk, 0),
                "can_add": not product.track_inventory or stock_quantity > cart_quantities.get(product.pk, 0),
            }
        )
    return {
        "tenant": tenant,
        "search_form": search_form or RegisterSearchForm(initial={"q": q}),
        "pricing_form": pricing_form or RegisterPricingForm(initial={"discount": meta["discount"], "tax": meta["tax"]}),
        "checkout_form": checkout_form or RegisterCheckoutForm(initial={"payment_method": meta["payment_method"], "reference": meta["reference"]}),
        "products": product_cards,
        "product_page": product_page,
        "has_catalog_products": has_catalog_products,
        "categories": categories,
        "selected_category": selected_category,
        "cart_lines": cart_lines,
        "cart_count": sum(line["quantity"] for line in cart_lines),
        "totals": totals,
        "current_payment_method": meta["payment_method"],
        "current_reference": meta["reference"],
        "register_workspace": True,
    }


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    TenantMembership.Role.CASHIER,
    action_name="open register",
)
def register(request):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    search_form = RegisterSearchForm(request.GET or None)
    pricing_form = RegisterPricingForm(request.POST or None)
    checkout_form = RegisterCheckoutForm(request.POST or None)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            product = Product.objects.select_related("stock").filter(
                tenant=tenant,
                is_active=True,
                pk=request.POST.get("product_id"),
            ).first()
            if product is not None:
                cart = _read_cart(request, tenant)
                current_quantity = next(
                    (line["quantity"] for line in cart if line["product_id"] == product.pk),
                    0,
                )
                available_quantity = _product_stock_quantity(product)
                if available_quantity is not None and current_quantity >= available_quantity:
                    messages.error(request, f"{product.name} has no more stock available to add.")
                    return _register_redirect(request)
                for line in cart:
                    if line["product_id"] == product.pk:
                        line["quantity"] += 1
                        break
                else:
                    cart.append({"product_id": product.pk, "quantity": 1, "unit_price": product.sale_price})
                _write_cart(request, tenant, cart)
                messages.success(request, f"Added {product.name} to the cart.")
            return _register_redirect(request)

        if action == "update-line":
            form = RegisterCartAdjustForm(request.POST)
            if form.is_valid():
                cart = _read_cart(request, tenant)
                product_id = form.cleaned_data["product_id"]
                quantity = form.cleaned_data["quantity"]
                product = Product.objects.select_related("stock").filter(
                    tenant=tenant,
                    is_active=True,
                    pk=product_id,
                ).first()
                if product is None:
                    messages.error(request, "This product is no longer available for sale.")
                    return _register_redirect(request)
                available_quantity = _product_stock_quantity(product)
                if available_quantity is not None and quantity > available_quantity:
                    messages.error(request, f"Only {available_quantity} unit(s) of {product.name} are available.")
                    return _register_redirect(request)
                updated = []
                for line in cart:
                    if line["product_id"] == product_id:
                        if quantity > 0:
                            line["quantity"] = quantity
                            updated.append(line)
                    else:
                        updated.append(line)
                _write_cart(request, tenant, updated)
            return _register_redirect(request)

        if action == "remove-line":
            product_id = int(request.POST.get("product_id", "0") or 0)
            cart = [line for line in _read_cart(request, tenant) if line["product_id"] != product_id]
            _write_cart(request, tenant, cart)
            return _register_redirect(request)

        if action == "clear-cart":
            _clear_register_state(request, tenant)
            return _register_redirect(request)

        if action == "save-pricing" and pricing_form.is_valid():
            meta = _read_register_meta(request, tenant)
            _write_register_meta(
                request,
                tenant,
                discount=pricing_form.cleaned_data.get("discount") or Decimal("0.00"),
                tax=pricing_form.cleaned_data.get("tax") or Decimal("0.00"),
                payment_method=meta["payment_method"],
                reference=meta["reference"],
            )
            messages.success(request, "Discount and tax updated.")
            return _register_redirect(request)

        if action == "save-checkout" and checkout_form.is_valid():
            meta = _read_register_meta(request, tenant)
            _write_register_meta(
                request,
                tenant,
                discount=meta["discount"],
                tax=meta["tax"],
                payment_method=checkout_form.cleaned_data["payment_method"],
                reference=checkout_form.cleaned_data.get("reference", ""),
            )
            messages.success(request, "Checkout settings saved.")
            return _register_redirect(request)

        if action == "complete-sale":
            checkout_form = RegisterCheckoutForm(request.POST)
            if checkout_form.is_valid():
                meta = _read_register_meta(request, tenant)
                cart_lines = _cart_products(tenant, _read_cart(request, tenant))
                if not cart_lines:
                    messages.error(request, "Add at least one item before checkout.")
                    return _register_redirect(request)
                try:
                    if len(cart_lines) != len(_read_cart(request, tenant)):
                        messages.error(request, "A cart product is no longer available. Remove it before checkout.")
                        return _register_redirect(request)
                    sale = complete_sale(
                        tenant=tenant,
                        cashier=request.user,
                        cart_items=[
                            {
                                "product": line["product"],
                                "quantity": line["quantity"],
                                "unit_price": line["unit_price"],
                            }
                            for line in cart_lines
                        ],
                        payment_method=checkout_form.cleaned_data["payment_method"],
                        discount=meta["discount"],
                        tax=meta["tax"],
                        reference=checkout_form.cleaned_data.get("reference", ""),
                        checkout_key=_checkout_key(request, tenant),
                    )
                except (InsufficientStockError, PaymentMethodNotAllowedError, DomainError) as exc:
                    messages.error(request, str(exc))
                    return _register_redirect(request)

                _clear_register_state(request, tenant)
                request.session.pop(f"sales_register_checkout_key_{tenant.id}", None)
                messages.success(request, f"Sale {sale.sale_number} completed.")
                return _register_redirect(request, reset_filters=True)

    context = _register_context(request, tenant, search_form=search_form, pricing_form=pricing_form, checkout_form=checkout_form)
    return _html_response(request, "sales/register.html", context)


def _document_queryset(queryset, tenant, query, fields):
    queryset = queryset.filter(tenant=tenant)
    if query:
        predicate = Q()
        for field in fields:
            predicate |= Q(**{f"{field}__icontains": query})
        queryset = queryset.filter(predicate)
    return queryset


def _apply_document_date_range(queryset, request, field="created_at"):
    """Apply validated inclusive dates without trusting arbitrary field input."""
    start = parse_date(request.GET.get("date_from", ""))
    end = parse_date(request.GET.get("date_to", ""))
    if start:
        queryset = queryset.filter(**{f"{field}__date__gte": start})
    if end:
        queryset = queryset.filter(**{f"{field}__date__lte": end})
    return queryset


def _cashier_only(request):
    membership = active_membership_for(request.user, request.tenant)
    return membership is not None and membership.role == TenantMembership.Role.CASHIER


def _visible_customers(tenant, request):
    """Return only customer records that the current document actor may discover."""
    customers = tenant.customer_set.all()
    if _cashier_only(request):
        customers = customers.filter(sales__cashier=request.user).distinct()
    return customers


def _filter_fk(queryset, request, parameter, field):
    value = request.GET.get(parameter, "")
    return queryset.filter(**{field: int(value)}) if value.isdigit() else queryset


def _filter_customer(queryset, request):
    value = request.GET.get("customer", "")
    if value == "walk_in":
        return queryset.filter(customer__isnull=True)
    return queryset.filter(customer_id=int(value)) if value.isdigit() else queryset


def _payment_status(document):
    sale = document if isinstance(document, Sale) else document.sale
    if sale is None:
        return "unpaid"
    try:
        return sale.payment.status
    except ObjectDoesNotExist:
        return "unpaid"


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.MANAGER, TenantMembership.Role.CASHIER, action_name="view sales")
def sale_list(request):
    tenant, response = _tenant_or_redirect(request)
    if response: return response
    query = request.GET.get("q", "").strip()
    sales = _document_queryset(
        Sale.objects.select_related("cashier", "customer", "receipt", "invoice", "payment").annotate(
            item_quantity=Coalesce(
                Sum("items__quantity"),
                Value(0),
                output_field=IntegerField(),
            )
        ),
        tenant,
        query,
        ("sale_number", "receipt__receipt_number", "invoice__invoice_number", "customer__name", "items__product__name", "items__product__sku"),
    ).distinct()
    if _cashier_only(request):
        sales = sales.filter(cashier=request.user)
    sales = _apply_document_date_range(sales, request)
    if request.GET.get("status") in Sale.Status.values: sales = sales.filter(status=request.GET["status"])
    sales = _filter_customer(sales, request)
    if not _cashier_only(request):
        sales = _filter_fk(sales, request, "cashier", "cashier_id")
    if request.GET.get("payment_method") in Payment.Method.values: sales = sales.filter(payment__method=request.GET["payment_method"])
    sales = sales.order_by("-created_at", "-id")
    return _html_response(request, "sales/sale_list.html", {"sales": Paginator(sales, 50).get_page(request.GET.get("page")), "query": query, "statuses": Sale.Status.choices, "payment_methods": Payment.Method.choices, "customers": _visible_customers(tenant, request), "customer_filter": True, "cashiers": tenant.tenantmembership_set.select_related("user").filter(is_active=True), "show_cashier_filter": not _cashier_only(request), "tenant": tenant})


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.MANAGER, TenantMembership.Role.CASHIER, action_name="view sale")
def sale_detail(request, sale_id):
    tenant, response = _tenant_or_redirect(request)
    if response: return response
    sales = Sale.objects.select_related("cashier", "customer", "receipt", "invoice", "payment").prefetch_related("items__product")
    if _cashier_only(request):
        sales = sales.filter(cashier=request.user)
    sale = get_object_or_404(sales, pk=sale_id, tenant=tenant)
    movements = StockMovement.objects.filter(tenant=tenant, reference_type=StockMovement.ReferenceType.SALE, reference_id=sale.pk).select_related("product")
    return _html_response(request, "sales/sale_detail.html", {"sale": sale, "tenant": tenant, "movements": movements})


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.MANAGER, TenantMembership.Role.CASHIER, action_name="view invoices")
def invoice_list(request):
    tenant, response = _tenant_or_redirect(request)
    if response: return response
    query = request.GET.get("q", "").strip()
    invoices = _document_queryset(Invoice.objects.select_related("created_by", "sale", "sale__cashier", "customer", "sale__receipt", "sale__payment"), tenant, query, ("invoice_number", "customer__name", "sale__sale_number", "items__product__name", "items__product__sku")).distinct()
    if _cashier_only(request):
        invoices = invoices.filter(sale__cashier=request.user)
    invoices = _apply_document_date_range(invoices, request)
    if request.GET.get("status") in Invoice.Status.values: invoices = invoices.filter(status=request.GET["status"])
    invoices = _filter_customer(invoices, request)
    if not _cashier_only(request):
        invoices = _filter_fk(invoices, request, "cashier", "sale__cashier_id")
    return _html_response(request, "sales/invoice_list.html", {"invoices": Paginator(invoices.order_by("-created_at", "-id"), 50).get_page(request.GET.get("page")), "query": query, "statuses": Invoice.Status.choices, "customers": _visible_customers(tenant, request), "customer_filter": True, "cashiers": tenant.tenantmembership_set.select_related("user").filter(is_active=True), "show_cashier_filter": not _cashier_only(request), "tenant": tenant})


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.MANAGER, TenantMembership.Role.CASHIER, action_name="view invoice")
def invoice_detail(request, invoice_id):
    tenant, response = _tenant_or_redirect(request)
    if response: return response
    invoices = Invoice.objects.select_related("sale", "sale__cashier", "customer", "sale__receipt", "sale__payment").prefetch_related("items__product")
    if _cashier_only(request):
        invoices = invoices.filter(sale__cashier=request.user)
    invoice = get_object_or_404(invoices, pk=invoice_id, tenant=tenant)
    checkout_form = RegisterCheckoutForm(request.POST or None)
    if request.method == "POST" and request.POST.get("action") == "confirm-payment" and invoice.status == Invoice.Status.DRAFT:
        if checkout_form.is_valid():
            try:
                sale = complete_sale(
                    tenant=tenant, cashier=request.user, invoice=invoice,
                    cart_items=[{"product": item.product, "quantity": item.quantity, "unit_price": item.unit_price} for item in invoice.items.all()],
                    payment_method=checkout_form.cleaned_data["payment_method"],
                    reference=checkout_form.cleaned_data.get("reference", ""),
                    discount=invoice.discount_amount, tax=invoice.tax_amount,
                    checkout_key=f"invoice-{invoice.pk}",
                )
            except (InsufficientStockError, PaymentMethodNotAllowedError, ValueError) as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Full payment recorded. Receipt {sale.receipt.receipt_number} issued.")
                return redirect("sales:receipt-detail", receipt_id=sale.receipt.id)
    return _html_response(request, "sales/invoice_detail.html", {"invoice": invoice, "checkout_form": checkout_form, "tenant": tenant})


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.MANAGER, action_name="view quotations")
def quotation_list(request):
    tenant, response = _tenant_or_redirect(request)
    if response: return response
    query = request.GET.get("q", "").strip()
    quotations = _document_queryset(Quotation.objects.select_related("customer", "created_by", "converted_invoice").prefetch_related("items__product"), tenant, query, ("quotation_number", "customer__name", "items__product__name", "items__product__sku")).distinct()
    quotations = _apply_document_date_range(quotations, request)
    if request.GET.get("status") in Quotation.Status.values: quotations = quotations.filter(status=request.GET["status"])
    quotations = _filter_customer(quotations, request)
    quotations = _filter_fk(quotations, request, "creator", "created_by_id")
    return _html_response(request, "sales/quotation_list.html", {"quotations": Paginator(quotations.order_by("-created_at", "-id"), 50).get_page(request.GET.get("page")), "query": query, "statuses": Quotation.Status.choices, "customers": tenant.customer_set.all(), "customer_filter": True, "creators": tenant.tenantmembership_set.select_related("user").filter(is_active=True), "tenant": tenant})


def _quotation_form_response(request, *, tenant, quotation=None):
    initial = None
    if quotation is not None:
        initial = [{"product": item.product_id, "quantity": item.quantity} for item in quotation.items.all()]
    form = QuotationForm(
        request.POST or None,
        tenant=tenant,
        initial={
            "customer": quotation.customer_id if quotation else None,
            "expires_at": quotation.expires_at if quotation else None,
            "discount": quotation.discount_amount if quotation else Decimal("0.00"),
            "tax": quotation.tax_amount if quotation else Decimal("0.00"),
        },
    )
    line_formset = QuotationLineFormSet(
        request.POST or None,
        prefix="lines",
        initial=initial,
        form_kwargs={"tenant": tenant},
    )
    if request.method == "POST" and form.is_valid() and line_formset.is_valid():
        lines = [
            {"product": line.cleaned_data["product"], "quantity": line.cleaned_data["quantity"]}
            for line in line_formset.forms
            if line.cleaned_data and not line.cleaned_data.get("DELETE")
        ]
        try:
            saved = save_quotation(
                tenant=tenant,
                actor=request.user,
                customer=form.cleaned_data["customer"],
                expires_at=form.cleaned_data["expires_at"],
                discount=form.cleaned_data.get("discount") or Decimal("0.00"),
                tax=form.cleaned_data.get("tax") or Decimal("0.00"),
                line_items=lines,
                quotation=quotation,
            )
        except (ValueError, Product.DoesNotExist, Customer.DoesNotExist) as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Quotation {saved.quotation_number} saved.")
            return redirect("sales:quotation-detail", quotation_id=saved.pk)
    return _html_response(
        request,
        "sales/quotation_form.html",
        {"form": form, "line_formset": line_formset, "quotation": quotation, "tenant": tenant},
    )


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.MANAGER, action_name="create quotation")
def quotation_create(request):
    tenant, response = _tenant_or_redirect(request)
    if response: return response
    return _quotation_form_response(request, tenant=tenant)


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.MANAGER, action_name="edit quotation")
def quotation_edit(request, quotation_id):
    tenant, response = _tenant_or_redirect(request)
    if response: return response
    quotation = get_object_or_404(
        Quotation.objects.prefetch_related("items__product"),
        pk=quotation_id,
        tenant=tenant,
        status=Quotation.Status.DRAFT,
        converted_invoice__isnull=True,
    )
    return _quotation_form_response(request, tenant=tenant, quotation=quotation)


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.MANAGER, action_name="change quotation status")
def quotation_status(request, quotation_id):
    tenant, response = _tenant_or_redirect(request)
    if response: return response
    if request.method != "POST":
        return redirect("sales:quotation-detail", quotation_id=quotation_id)
    quotation = get_object_or_404(Quotation, pk=quotation_id, tenant=tenant)
    try:
        change_quotation_status(quotation=quotation, actor=request.user, status=request.POST.get("status", ""))
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Quotation status updated.")
    return redirect("sales:quotation-detail", quotation_id=quotation_id)


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.MANAGER, action_name="view quotation")
def quotation_detail(request, quotation_id):
    tenant, response = _tenant_or_redirect(request)
    if response: return response
    quotation = get_object_or_404(Quotation.objects.select_related("customer", "created_by", "converted_invoice").prefetch_related("items__product"), pk=quotation_id, tenant=tenant)
    if request.method == "POST" and request.POST.get("action") == "convert":
        try:
            invoice = convert_quotation_to_invoice(quotation=quotation, actor=request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Quotation converted to draft invoice {invoice.invoice_number}.")
            return redirect("sales:invoice-detail", invoice_id=invoice.id)
    allowed_transitions = QUOTATION_STATUS_TRANSITIONS.get(quotation.status, set())
    transitions = [value for value, _label in Quotation.Status.choices if value in allowed_transitions]
    return _html_response(request, "sales/quotation_detail.html", {"quotation": quotation, "transitions": transitions, "tenant": tenant})


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.MANAGER, TenantMembership.Role.CASHIER, action_name="view receipts")
def receipt_list(request):
    tenant, response = _tenant_or_redirect(request)
    if response: return response
    query = request.GET.get("q", "").strip()
    receipts = _document_queryset(Receipt.objects.select_related("sale", "sale__cashier", "sale__customer", "sale__invoice", "sale__payment"), tenant, query, ("receipt_number", "sale__sale_number", "sale__invoice__invoice_number", "sale__customer__name")).distinct()
    receipts = _apply_document_date_range(receipts, request, field="issued_at")
    if _cashier_only(request):
        receipts = receipts.filter(sale__cashier=request.user)
    else:
        receipts = _filter_fk(receipts, request, "cashier", "sale__cashier_id")
    if request.GET.get("payment_method") in Payment.Method.values: receipts = receipts.filter(sale__payment__method=request.GET["payment_method"])
    return _html_response(request, "sales/receipt_list.html", {"receipts": Paginator(receipts.order_by("-issued_at", "-id"), 50).get_page(request.GET.get("page")), "query": query, "payment_methods": Payment.Method.choices, "cashiers": tenant.tenantmembership_set.select_related("user").filter(is_active=True), "show_cashier_filter": not _cashier_only(request), "tenant": tenant})


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.MANAGER, TenantMembership.Role.CASHIER, action_name="view receipt")
def receipt_detail(request, receipt_id):
    tenant, response = _tenant_or_redirect(request)
    if response: return response
    receipts = Receipt.objects.select_related("sale", "sale__cashier", "sale__customer", "sale__invoice", "sale__payment").prefetch_related("sale__items__product")
    if _cashier_only(request):
        receipts = receipts.filter(sale__cashier=request.user)
    receipt = get_object_or_404(receipts, pk=receipt_id, tenant=tenant)
    return _html_response(request, "sales/receipt_detail.html", {"receipt": receipt, "tenant": tenant})


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.MANAGER, TenantMembership.Role.CASHIER, action_name="print receipt")
def receipt_print(request, receipt_id):
    tenant, response = _tenant_or_redirect(request)
    if response: return response
    receipts = Receipt.objects.select_related("sale", "sale__cashier", "sale__customer", "sale__invoice", "sale__payment").prefetch_related("sale__items__product")
    if _cashier_only(request):
        receipts = receipts.filter(sale__cashier=request.user)
    receipt = get_object_or_404(receipts, pk=receipt_id, tenant=tenant)
    if receipt.printed_at is None:
        with transaction.atomic():
            receipt.printed_at = timezone.now()
            receipt.save(update_fields=["printed_at", "updated_at"])
            log_audit_event(
                tenant=tenant, actor=request.user, action=AuditEvent.Action.RECEIPT_PRINTED,
                target=receipt, after_data={"receipt_number": receipt.receipt_number},
            )
    return _html_response(request, "sales/receipt_print.html", {"receipt": receipt, "tenant": tenant})
