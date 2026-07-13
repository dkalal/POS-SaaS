from contextlib import contextmanager
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.test.signals import template_rendered
from django.utils import timezone

from accounts.models import TenantMembership
from accounts.rbac import tenant_role_required
from catalog.models import Product
from core.exceptions import DomainError, InsufficientStockError, PaymentMethodNotAllowedError
from payments.models import Payment
from sales.forms import RegisterCartAdjustForm, RegisterCheckoutForm, RegisterPricingForm, RegisterSearchForm
from sales.services import calculate_sale_totals, complete_sale


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


def _cart_products(tenant, cart):
    if not cart:
        return []
    products = Product.objects.select_related("category").filter(
        tenant=tenant,
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
    q = (search_form.cleaned_data["q"] if search_form and search_form.is_valid() else (request.GET.get("q") or "")).strip()
    products = Product.objects.select_related("category", "stock").filter(tenant=tenant, is_active=True)
    if q:
        products = products.filter(
            (
                Q(name__icontains=q)
                | Q(sku__icontains=q)
                | Q(barcode__icontains=q)
                | Q(category__name__icontains=q)
            )
        )
    product_cards = []
    for product in products.order_by("name", "sku")[:24]:
        stock_quantity = 0
        try:
            stock_quantity = product.stock.quantity
        except ObjectDoesNotExist:
            stock_quantity = 0
        product_cards.append(
            {
                "product": product,
                "stock_quantity": stock_quantity,
                "category_name": product.category.name if product.category_id else "Uncategorized",
            }
        )
    return {
        "tenant": tenant,
        "search_form": search_form or RegisterSearchForm(initial={"q": q}),
        "pricing_form": pricing_form or RegisterPricingForm(initial={"discount": meta["discount"], "tax": meta["tax"]}),
        "checkout_form": checkout_form or RegisterCheckoutForm(initial={"payment_method": meta["payment_method"], "reference": meta["reference"]}),
        "products": product_cards,
        "cart_lines": cart_lines,
        "cart_count": sum(line["quantity"] for line in cart_lines),
        "totals": totals,
        "current_payment_method": meta["payment_method"],
        "current_reference": meta["reference"],
        "can_open_register": True,
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
            product = Product.objects.filter(tenant=tenant, is_active=True, pk=request.POST.get("product_id")).first()
            if product is not None:
                cart = _read_cart(request, tenant)
                for line in cart:
                    if line["product_id"] == product.pk:
                        line["quantity"] += 1
                        break
                else:
                    cart.append({"product_id": product.pk, "quantity": 1, "unit_price": product.sale_price})
                _write_cart(request, tenant, cart)
                messages.success(request, f"Added {product.name} to the cart.")
            return redirect("sales:register")

        if action == "update-line":
            form = RegisterCartAdjustForm(request.POST)
            if form.is_valid():
                cart = _read_cart(request, tenant)
                product_id = form.cleaned_data["product_id"]
                quantity = form.cleaned_data["quantity"]
                updated = []
                for line in cart:
                    if line["product_id"] == product_id:
                        if quantity > 0:
                            line["quantity"] = quantity
                            updated.append(line)
                    else:
                        updated.append(line)
                _write_cart(request, tenant, updated)
            return redirect("sales:register")

        if action == "remove-line":
            product_id = int(request.POST.get("product_id", "0") or 0)
            cart = [line for line in _read_cart(request, tenant) if line["product_id"] != product_id]
            _write_cart(request, tenant, cart)
            return redirect("sales:register")

        if action == "clear-cart":
            _clear_register_state(request, tenant)
            return redirect("sales:register")

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
            return redirect("sales:register")

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
            return redirect("sales:register")

        if action == "complete-sale":
            checkout_form = RegisterCheckoutForm(request.POST)
            if checkout_form.is_valid():
                meta = _read_register_meta(request, tenant)
                cart_lines = _cart_products(tenant, _read_cart(request, tenant))
                if not cart_lines:
                    messages.error(request, "Add at least one item before checkout.")
                    return redirect("sales:register")
                try:
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
                    )
                except (InsufficientStockError, PaymentMethodNotAllowedError, DomainError) as exc:
                    messages.error(request, str(exc))
                    return redirect("sales:register")

                _clear_register_state(request, tenant)
                messages.success(request, f"Sale {sale.sale_number} completed.")
                return redirect("sales:register")

    context = _register_context(request, tenant, search_form=search_form, pricing_form=pricing_form, checkout_form=checkout_form)
    return _html_response(request, "sales/register.html", context)
