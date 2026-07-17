from contextlib import contextmanager
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.test.signals import template_rendered
from django.views.decorators.http import require_POST
from django.utils import timezone

from accounts.models import TenantMembership
from accounts.rbac import tenant_role_required
from core.exceptions import DomainError, PurchaseAlreadyReceivedError, PurchaseNotReceivedError
from purchasing.forms import PurchaseCreateForm, PurchaseFilterForm, PurchaseItemFormSet
from purchasing.models import Purchase
from purchasing.services import (
    cancel_received_purchase,
    create_draft_purchase,
    duplicate_purchase,
    receive_purchase,
    update_draft_purchase,
)
from core.money import format_money
from suppliers.models import Supplier


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


def _clean_querydict(request):
    params = request.GET.copy()
    params.pop("page", None)
    return params


def _paginate(queryset, request, per_page=12):
    paginator = Paginator(queryset, per_page)
    page_obj = paginator.get_page(request.GET.get("page"))
    return page_obj, _clean_querydict(request).urlencode()


def _purchase_context(request, *, purchase_form=None, item_formset=None, purchase_filter=None):
    tenant = getattr(request, "tenant", None)
    return {
        "tenant": tenant,
        "purchase_form": purchase_form or PurchaseCreateForm(tenant=tenant, initial={"order_date": timezone.localdate()}),
        "item_formset": item_formset or PurchaseItemFormSet(form_kwargs={"tenant": tenant}),
        "purchase_filter": purchase_filter or PurchaseFilterForm(request.GET, supplier_choices=_supplier_filter_choices(tenant)),
        "can_manage_purchases": True,
    }


def _supplier_filter_choices(tenant):
    if tenant is None:
        return []
    return list(
        Supplier.objects.filter(tenant=tenant).order_by("name").values_list("id", "name")
    )


def _purchase_item_initial(purchase):
    return [
        {
            "product": item.product_id,
            "quantity": item.quantity,
            "unit_cost": item.unit_cost,
        }
        for item in purchase.items.select_related("product").all().order_by("id")
    ]


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    action_name="view purchases",
)
def purchase_list(request):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    purchase_filter = PurchaseFilterForm(request.GET, supplier_choices=_supplier_filter_choices(tenant))
    purchases = Purchase.objects.select_related("supplier", "created_by", "received_by", "cancelled_by").filter(tenant=tenant)
    if purchase_filter.is_valid():
        q = (purchase_filter.cleaned_data.get("q") or "").strip()
        status = purchase_filter.cleaned_data.get("status") or ""
        supplier_id = purchase_filter.cleaned_data.get("supplier") or ""
        date_from = purchase_filter.cleaned_data.get("date_from")
        date_to = purchase_filter.cleaned_data.get("date_to")
        if q:
            purchases = purchases.filter(
                Q(purchase_number__icontains=q)
                | Q(notes__icontains=q)
                | Q(cancelled_reason__icontains=q)
                | Q(supplier__name__icontains=q)
                | Q(supplier__supplier_code__icontains=q)
                | Q(items__product__sku__icontains=q)
                | Q(items__product__name__icontains=q)
                | Q(items__product__barcode__icontains=q)
            ).distinct()
        if status:
            purchases = purchases.filter(status=status)
        if supplier_id:
            purchases = purchases.filter(supplier_id=supplier_id)
        if date_from:
            purchases = purchases.filter(order_date__gte=date_from)
        if date_to:
            purchases = purchases.filter(order_date__lte=date_to)

    purchases = purchases.annotate(total_amount=Coalesce(Sum("items__line_total"), Decimal("0.00"))).order_by("-order_date", "-id")
    purchase_page, query_string = _paginate(purchases, request)
    for purchase in purchase_page:
        purchase.total_amount = format_money(purchase.total_amount, tenant.currency)
    context = _purchase_context(request, purchase_filter=purchase_filter)
    context["purchases"] = purchase_page
    context["purchase_query_string"] = query_string
    return _html_response(request, "purchasing/purchase_list.html", context)


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    action_name="create draft purchase",
)
def purchase_create(request):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    purchase_form = PurchaseCreateForm(request.POST or None, tenant=tenant)
    item_formset = PurchaseItemFormSet(request.POST or None, form_kwargs={"tenant": tenant})

    if request.method == "POST" and purchase_form.is_valid() and item_formset.is_valid():
        items = []
        for form in item_formset.forms:
            cleaned = getattr(form, "cleaned_data", None) or {}
            product = cleaned.get("product")
            if product is None:
                continue
            items.append(
                {
                    "product": product,
                    "quantity": cleaned["quantity"],
                    "unit_cost": cleaned["unit_cost"],
                }
            )

        if not items:
            purchase_form.add_error(None, "Add at least one purchase line item.")
        else:
            purchase = create_draft_purchase(
                tenant=tenant,
                supplier=purchase_form.cleaned_data["supplier"],
                items=items,
                created_by=request.user,
                order_date=purchase_form.cleaned_data["order_date"],
                expected_date=purchase_form.cleaned_data["expected_date"],
                notes=purchase_form.cleaned_data["notes"],
            )
            messages.success(request, f"Draft purchase {purchase.purchase_number} created.")
            return redirect("purchasing:purchase-detail", purchase_id=purchase.id)

    return _html_response(
        request,
        "purchasing/purchase_form.html",
        {
            **_purchase_context(request, purchase_form=purchase_form, item_formset=item_formset),
            "page_title": "New Purchase | POS SaaS",
            "page_heading": "Create draft purchase",
            "page_description": "Choose a supplier, capture line items, and let the purchase service manage the receiving state later.",
            "submit_label": "Create draft purchase",
        },
    )


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    action_name="edit draft purchase",
)
def purchase_edit(request, purchase_id):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    purchase = get_object_or_404(Purchase.objects.select_related("supplier"), pk=purchase_id, tenant=tenant)
    if purchase.status != Purchase.Status.DRAFT:
        messages.error(request, "Only draft purchases can be edited.")
        return redirect("purchasing:purchase-detail", purchase_id=purchase.id)

    initial_items = _purchase_item_initial(purchase)
    purchase_form = PurchaseCreateForm(
        request.POST or None,
        tenant=tenant,
        initial=None if request.method == "POST" else {
            "supplier": purchase.supplier_id,
            "order_date": purchase.order_date,
            "expected_date": purchase.expected_date,
            "notes": purchase.notes,
        },
        instance=purchase,
    )
    item_formset = PurchaseItemFormSet(
        request.POST or None,
        initial=None if request.method == "POST" else initial_items,
        form_kwargs={"tenant": tenant},
    )

    if request.method == "POST" and purchase_form.is_valid() and item_formset.is_valid():
        items = []
        for form in item_formset.forms:
            cleaned = getattr(form, "cleaned_data", None) or {}
            product = cleaned.get("product")
            if product is None:
                continue
            items.append(
                {
                    "product": product,
                    "quantity": cleaned["quantity"],
                    "unit_cost": cleaned["unit_cost"],
                }
            )
        if not items:
            purchase_form.add_error(None, "Add at least one purchase line item.")
        else:
            purchase = update_draft_purchase(
                purchase_id=purchase.id,
                tenant=tenant,
                supplier=purchase_form.cleaned_data["supplier"],
                items=items,
                updated_by=request.user,
                order_date=purchase_form.cleaned_data["order_date"],
                expected_date=purchase_form.cleaned_data["expected_date"],
                notes=purchase_form.cleaned_data["notes"],
            )
            messages.success(request, f"Draft purchase {purchase.purchase_number} updated.")
            return redirect("purchasing:purchase-detail", purchase_id=purchase.id)

    return _html_response(
        request,
        "purchasing/purchase_form.html",
        {
            **_purchase_context(request, purchase_form=purchase_form, item_formset=item_formset),
            "page_title": f"Edit {purchase.purchase_number} | POS SaaS",
            "page_heading": "Edit draft purchase",
            "page_description": "Update the supplier, dates, notes, and line items before receiving.",
            "submit_label": "Save draft purchase",
        },
    )


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    action_name="view purchase details",
)
def purchase_detail(request, purchase_id):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    purchase = get_object_or_404(
        Purchase.objects.select_related("supplier", "created_by", "received_by", "cancelled_by"),
        pk=purchase_id,
        tenant=tenant,
    )
    items = purchase.items.select_related("product").all()
    total_amount = sum((item.line_total for item in items), Decimal("0.00"))

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "receive":
                purchase = receive_purchase(purchase.id, request.user)
                messages.success(request, f"Purchase {purchase.purchase_number} received.")
                return redirect("purchasing:purchase-detail", purchase_id=purchase.id)
            if action == "cancel":
                purchase = cancel_received_purchase(
                    purchase.id,
                    request.user,
                    reason=request.POST.get("cancel_reason", ""),
                )
                messages.success(request, f"Purchase {purchase.purchase_number} cancelled.")
                return redirect("purchasing:purchase-detail", purchase_id=purchase.id)
        except (PurchaseAlreadyReceivedError, PurchaseNotReceivedError, DomainError) as exc:
            messages.error(request, str(exc))
            return redirect("purchasing:purchase-detail", purchase_id=purchase.id)

    purchase_items = [
        {
            "product": item.product,
            "quantity": item.quantity,
            "unit_cost": format_money(item.unit_cost, tenant.currency),
            "line_total": format_money(item.line_total, tenant.currency),
        }
        for item in items
    ]

    return _html_response(
        request,
        "purchasing/purchase_detail.html",
        {
            "tenant": tenant,
            "purchase": purchase,
            "items": purchase_items,
            "total_amount": format_money(total_amount, tenant.currency),
            "can_manage_purchases": True,
        },
    )


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    action_name="duplicate purchase",
)
@require_POST
def purchase_duplicate(request, purchase_id):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    source = get_object_or_404(Purchase.objects.select_related("supplier"), pk=purchase_id, tenant=tenant)
    duplicated = duplicate_purchase(source.id, request.user)
    messages.success(request, f"Purchase {source.purchase_number} duplicated.")
    return redirect("purchasing:purchase-edit", purchase_id=duplicated.id)
