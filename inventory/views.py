from contextlib import contextmanager
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Case, Count, F, IntegerField, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.test.signals import template_rendered
from django.views.decorators.http import require_POST

from accounts.models import TenantMembership
from accounts.rbac import OWNER_ROLES, tenant_role_required
from core.exceptions import (
    DomainError,
    InsufficientStockError,
    StockAdjustmentAlreadyPostedError,
    StockAdjustmentNotDraftError,
    StockAdjustmentNotPostedError,
)
from catalog.models import Product
from inventory.forms import AdjustmentFilterForm, InventoryFilterForm, MovementFilterForm, StockAdjustmentForm, StockAdjustmentLineFormSet
from inventory.models import StockAdjustment, StockMovement
from inventory.services import cancel_adjustment, create_draft_adjustment, post_adjustment, update_draft_adjustment


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


def _current_membership(request):
    tenant = getattr(request, "tenant", None)
    if tenant is None or request.user.is_anonymous:
        return None
    return TenantMembership.objects.filter(tenant=tenant, user=request.user, is_active=True).first()


def _clean_querydict(request):
    params = request.GET.copy()
    params.pop("page", None)
    return params


def _paginate(queryset, request, per_page=12):
    paginator = Paginator(queryset, per_page)
    page_obj = paginator.get_page(request.GET.get("page"))
    return page_obj, _clean_querydict(request).urlencode()


def _inventory_products(tenant):
    last_movement = StockMovement.objects.filter(tenant=tenant, product_id=OuterRef("pk")).order_by("-created_at", "-id")
    return Product.objects.select_related("category", "stock").filter(tenant=tenant, track_inventory=True).annotate(
        current_stock=Coalesce("stock__quantity", Value(0), output_field=IntegerField()),
        last_movement_at=Subquery(last_movement.values("created_at")[:1]),
        last_movement_type=Subquery(last_movement.values("movement_type")[:1]),
    )


def _filter_inventory_products(products, form):
    if not form.is_valid():
        return products
    q = (form.cleaned_data.get("q") or "").strip()
    if q:
        products = products.filter(Q(sku__icontains=q) | Q(name__icontains=q) | Q(barcode__icontains=q)).annotate(
            search_priority=Case(
                When(sku__iexact=q, then=Value(0)), When(sku__istartswith=q, then=Value(1)), When(sku__icontains=q, then=Value(2)),
                When(name__iexact=q, then=Value(3)), When(name__istartswith=q, then=Value(4)), When(name__icontains=q, then=Value(5)),
                When(barcode__iexact=q, then=Value(6)), When(barcode__istartswith=q, then=Value(7)), When(barcode__icontains=q, then=Value(8)),
                default=Value(9), output_field=IntegerField(),
            )
        ).order_by("search_priority", "name", "sku", "pk")
    if form.cleaned_data.get("category"):
        products = products.filter(category=form.cleaned_data["category"])
    if form.cleaned_data.get("status") == "active":
        products = products.filter(is_active=True)
    elif form.cleaned_data.get("status") == "archived":
        products = products.filter(is_active=False)
    status = form.cleaned_data.get("stock_status")
    if status == "in_stock":
        products = products.filter(current_stock__gt=F("reorder_level"))
    elif status == "low_stock":
        products = products.filter(reorder_level__gt=0, current_stock__gt=0, current_stock__lte=F("reorder_level"))
    elif status == "out_of_stock":
        products = products.filter(current_stock=0)
    return products


def _movement_reference(movement):
    labels = {"purchase": "Purchase", "sale": "Sale", "stock_adjustment": "Stock adjustment"}
    return f"{labels.get(movement.reference_type, movement.reference_type)} #{movement.reference_id}"


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.MANAGER, action_name="view inventory")
def inventory_overview(request):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response
    inventory_filter = InventoryFilterForm(request.GET, tenant=tenant)
    products = _filter_inventory_products(_inventory_products(tenant), inventory_filter)
    all_products = _inventory_products(tenant)
    summary = all_products.aggregate(
        total=Count("id"),
        in_stock=Count("id", filter=Q(current_stock__gt=F("reorder_level"))),
        low_stock=Count("id", filter=Q(reorder_level__gt=0, current_stock__gt=0, current_stock__lte=F("reorder_level"))),
        out_of_stock=Count("id", filter=Q(current_stock=0)),
    )
    inventory_page, query_string = _paginate(products, request, per_page=20)
    alerts = all_products.filter(reorder_level__gt=0, current_stock__gt=0, current_stock__lte=F("reorder_level")).order_by("current_stock", "name")[:6]
    no_physical_products = not all_products.exists()
    no_stock_received = not all_products.filter(current_stock__gt=0).exists()
    return _html_response(request, "inventory/overview.html", {
        "tenant": tenant, "inventory_filter": inventory_filter, "products": inventory_page,
        "inventory_query_string": query_string, "summary": summary, "low_stock_alerts": alerts,
        "no_physical_products": no_physical_products, "no_stock_received": no_stock_received,
        "can_manage_inventory": True,
    })


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.MANAGER, action_name="view stock movement history")
def product_stock_history(request, product_id):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response
    product = get_object_or_404(Product.objects.select_related("category", "stock"), pk=product_id, tenant=tenant, track_inventory=True)
    movements = StockMovement.objects.filter(tenant=tenant, product=product).select_related("created_by").order_by("-created_at", "-id")
    movement_page, query_string = _paginate(movements, request, per_page=25)
    for movement in movement_page:
        movement.reference_label = _movement_reference(movement)
    return _html_response(request, "inventory/product_history.html", {
        "tenant": tenant, "product": product, "current_stock": product.stock.quantity if hasattr(product, "stock") else 0,
        "movements": movement_page, "movement_query_string": query_string, "can_manage_inventory": True,
    })


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.MANAGER, action_name="view stock movement ledger")
def movement_ledger(request):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response
    movement_filter = MovementFilterForm(request.GET, tenant=tenant)
    movements = StockMovement.objects.filter(tenant=tenant).select_related("product", "product__category", "created_by")
    if movement_filter.is_valid():
        data = movement_filter.cleaned_data
        if data.get("date_from"):
            movements = movements.filter(created_at__date__gte=data["date_from"])
        if data.get("date_to"):
            movements = movements.filter(created_at__date__lte=data["date_to"])
        if data.get("product"):
            movements = movements.filter(product=data["product"])
        if data.get("category"):
            movements = movements.filter(product__category=data["category"])
        if data.get("movement_type"):
            movements = movements.filter(movement_type=data["movement_type"])
        if (reference := (data.get("reference") or "").strip()):
            reference_filter = Q(note__icontains=reference)
            if reference.isdigit():
                reference_filter |= Q(reference_id=int(reference))
            movements = movements.filter(reference_filter)
    movement_page, query_string = _paginate(movements, request, per_page=30)
    for movement in movement_page:
        movement.reference_label = _movement_reference(movement)
    return _html_response(request, "inventory/movement_ledger.html", {
        "tenant": tenant, "movement_filter": movement_filter, "movements": movement_page,
        "movement_query_string": query_string, "can_manage_inventory": True,
    })


def _line_initial(adjustment):
    return [
        {
            "product": item.product_id,
            "direction": "increase" if item.quantity_delta > 0 else "decrease",
            "quantity": abs(item.quantity_delta),
            "note": item.note,
        }
        for item in adjustment.items.select_related("product").all().order_by("id")
    ]


def _adjustment_context(request, *, adjustment_form=None, line_formset=None, adjustment_filter=None):
    tenant = getattr(request, "tenant", None)
    current_membership = _current_membership(request)
    can_post_adjustment = request.user.is_superuser or (
        current_membership is not None and current_membership.role in OWNER_ROLES
    )
    return {
        "tenant": tenant,
        "adjustment_form": adjustment_form or StockAdjustmentForm(tenant=tenant),
        "line_formset": line_formset or StockAdjustmentLineFormSet(form_kwargs={"tenant": tenant}),
        "adjustment_filter": adjustment_filter or AdjustmentFilterForm(request.GET),
        "can_manage_inventory": True,
        "can_post_adjustment": can_post_adjustment,
        "can_cancel_adjustment": can_post_adjustment,
    }


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    action_name="view stock adjustments",
)
def adjustment_list(request):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    adjustment_filter = AdjustmentFilterForm(request.GET)
    adjustments = StockAdjustment.objects.select_related("created_by", "posted_by", "cancelled_by").filter(tenant=tenant)
    if adjustment_filter.is_valid():
        q = (adjustment_filter.cleaned_data.get("q") or "").strip()
        status = adjustment_filter.cleaned_data.get("status") or ""
        if q:
            adjustments = adjustments.filter(
                Q(adjustment_number__icontains=q)
                | Q(reason__icontains=q)
                | Q(notes__icontains=q)
                | Q(cancel_reason__icontains=q)
            )
        if status:
            adjustments = adjustments.filter(status=status)

    adjustments = adjustments.annotate(item_count=Count("items"), net_delta=Sum("items__quantity_delta")).order_by("-id")
    adjustment_page, query_string = _paginate(adjustments, request)
    return _html_response(
        request,
        "inventory/adjustment_list.html",
        {
            **_adjustment_context(request, adjustment_filter=adjustment_filter),
            "adjustments": adjustment_page,
            "adjustment_query_string": query_string,
        },
    )


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    action_name="create stock adjustment",
)
def adjustment_create(request):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    adjustment_form = StockAdjustmentForm(request.POST or None, tenant=tenant)
    line_formset = StockAdjustmentLineFormSet(request.POST or None, form_kwargs={"tenant": tenant})
    if request.method == "POST" and adjustment_form.is_valid() and line_formset.is_valid():
        items = []
        for form in line_formset.forms:
            cleaned = getattr(form, "cleaned_data", None) or {}
            product = cleaned.get("product")
            if product is None:
                continue
            items.append(
                {
                    "product": product,
                    "direction": cleaned["direction"],
                    "quantity": cleaned["quantity"],
                    "note": cleaned.get("note", ""),
                }
            )
        if not items:
            adjustment_form.add_error(None, "Add at least one stock adjustment line.")
        else:
            adjustment = create_draft_adjustment(
                tenant=tenant,
                reason=adjustment_form.cleaned_data["reason"],
                notes=adjustment_form.cleaned_data["notes"],
                items=items,
                created_by=request.user,
            )
            messages.success(request, f"Draft adjustment {adjustment.adjustment_number} created.")
            return redirect("inventory:adjustment-detail", adjustment_id=adjustment.id)

    return _html_response(
        request,
        "inventory/adjustment_form.html",
        {
            **_adjustment_context(request, adjustment_form=adjustment_form, line_formset=line_formset),
            "page_title": "New Stock Adjustment | POS SaaS",
            "page_heading": "Create stock adjustment",
            "page_description": "Capture a draft stock correction, then post it only after the count has been reviewed.",
            "submit_label": "Create draft adjustment",
        },
    )


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    action_name="edit stock adjustment",
)
def adjustment_edit(request, adjustment_id):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    adjustment = get_object_or_404(
        StockAdjustment.objects.select_related("created_by", "posted_by", "cancelled_by"),
        pk=adjustment_id,
        tenant=tenant,
    )
    if adjustment.status != StockAdjustment.Status.DRAFT:
        messages.error(request, "Only draft adjustments can be edited.")
        return redirect("inventory:adjustment-detail", adjustment_id=adjustment.id)

    adjustment_form = StockAdjustmentForm(
        request.POST or None,
        tenant=tenant,
        initial=None if request.method == "POST" else {"reason": adjustment.reason, "notes": adjustment.notes},
    )
    line_formset = StockAdjustmentLineFormSet(
        request.POST or None,
        initial=None if request.method == "POST" else _line_initial(adjustment),
        form_kwargs={"tenant": tenant},
    )
    if request.method == "POST" and adjustment_form.is_valid() and line_formset.is_valid():
        items = []
        for form in line_formset.forms:
            cleaned = getattr(form, "cleaned_data", None) or {}
            product = cleaned.get("product")
            if product is None:
                continue
            items.append(
                {
                    "product": product,
                    "direction": cleaned["direction"],
                    "quantity": cleaned["quantity"],
                    "note": cleaned.get("note", ""),
                }
            )
        if not items:
            adjustment_form.add_error(None, "Add at least one stock adjustment line.")
        else:
            adjustment = update_draft_adjustment(
                adjustment_id=adjustment.id,
                tenant=tenant,
                reason=adjustment_form.cleaned_data["reason"],
                notes=adjustment_form.cleaned_data["notes"],
                items=items,
                updated_by=request.user,
            )
            messages.success(request, f"Draft adjustment {adjustment.adjustment_number} updated.")
            return redirect("inventory:adjustment-detail", adjustment_id=adjustment.id)

    return _html_response(
        request,
        "inventory/adjustment_form.html",
        {
            **_adjustment_context(request, adjustment_form=adjustment_form, line_formset=line_formset),
            "page_title": f"Edit {adjustment.adjustment_number} | POS SaaS",
            "page_heading": "Edit draft adjustment",
            "page_description": "Update the stock correction details before posting the movement ledger.",
            "submit_label": "Save draft adjustment",
        },
    )


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    action_name="view stock adjustment details",
)
def adjustment_detail(request, adjustment_id):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    adjustment = get_object_or_404(
        StockAdjustment.objects.select_related("created_by", "posted_by", "cancelled_by"),
        pk=adjustment_id,
        tenant=tenant,
    )
    current_membership = _current_membership(request)
    can_post_adjustment = request.user.is_superuser or (
        current_membership is not None and current_membership.role in OWNER_ROLES
    )
    items = adjustment.items.select_related("product").all().order_by("id")
    item_total = items.aggregate(total=Sum("quantity_delta"))["total"] or 0

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "post":
                adjustment = post_adjustment(adjustment.id, request.user)
                messages.success(request, f"Adjustment {adjustment.adjustment_number} posted.")
                return redirect("inventory:adjustment-detail", adjustment_id=adjustment.id)
            if action == "cancel":
                adjustment = cancel_adjustment(
                    adjustment.id,
                    request.user,
                    reason=request.POST.get("cancel_reason", ""),
                )
                messages.success(request, f"Adjustment {adjustment.adjustment_number} cancelled.")
                return redirect("inventory:adjustment-detail", adjustment_id=adjustment.id)
        except (
            InsufficientStockError,
            StockAdjustmentAlreadyPostedError,
            StockAdjustmentNotDraftError,
            StockAdjustmentNotPostedError,
            DomainError,
        ) as exc:
            messages.error(request, str(exc))
            return redirect("inventory:adjustment-detail", adjustment_id=adjustment.id)

    return _html_response(
        request,
        "inventory/adjustment_detail.html",
        {
            "tenant": tenant,
            "adjustment": adjustment,
            "items": items,
            "item_total": item_total,
            "can_manage_inventory": True,
            "can_post_adjustment": can_post_adjustment,
            "can_cancel_adjustment": can_post_adjustment,
        },
    )
