import csv
from contextlib import contextmanager
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test.signals import template_rendered
from django.utils import timezone

from accounts.models import TenantMembership
from accounts.rbac import tenant_role_required
from payments.models import Payment
from purchasing.models import Purchase
from reports.forms import ReportFilterForm
from reports.services import (
    completed_sales,
    inventory_products,
    inventory_summary,
    product_performance_data,
    profit_data,
    purchase_report_data,
    received_purchases,
    sale_items,
    sales_report_data,
)
from sales.models import Sale


REPORT_ROLES = (TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.MANAGER)


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
        template_rendered.sender_receivers_cache.clear()
        template_rendered.sender_receivers_cache.update(cache)


def _render(request, template, context, status=200):
    with timezone.override(request.tenant.timezone), _suppress_template_render_signal():
        return HttpResponse(render_to_string(template, context, request=request), status=status)


def _form(request, tenant, include=()):
    data = request.GET if request.GET else {"period": "month"}
    form = ReportFilterForm(data, tenant=tenant, include=include)
    form.is_valid()
    return form


def _page(queryset, request, per_page=30):
    page = Paginator(queryset, per_page).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return page, params.urlencode()


def _base_context(request, form, report_name, export_url=None):
    return {
        "tenant": request.tenant,
        "filter_form": form,
        "report_name": report_name,
        "export_url": export_url,
        "filter_query": request.GET.urlencode(),
        "range_label": form.range_label if form.is_valid() else "Invalid date range",
    }


def _csv_response(report_slug, form, headers):
    filename = f"{report_slug}_{form.cleaned_data['range_start']:%Y%m%d}_{form.cleaned_data['range_end']:%Y%m%d}.csv"
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(headers)
    return response, writer


def _safe(value):
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


def _local_iso(value, tenant):
    try:
        zone = ZoneInfo(tenant.timezone)
    except ZoneInfoNotFoundError:
        zone = timezone.get_default_timezone()
    return timezone.localtime(value, zone).isoformat()


@login_required
@tenant_role_required(*REPORT_ROLES, action_name="view reports")
def landing(request):
    tenant = request.tenant
    has_transactions = (
        Sale.objects.filter(tenant=tenant).exists()
        or Purchase.objects.filter(tenant=tenant).exists()
    )
    profit_form = _form(request, tenant)
    profit_available = profit_form.is_valid() and profit_data(tenant, profit_form)["available"]
    return _render(request, "reports/landing.html", {
        "tenant": tenant, "has_transactions": has_transactions, "profit_available": profit_available
    })


@login_required
@tenant_role_required(*REPORT_ROLES, action_name="view sales reports")
def sales_report(request):
    form = _form(request, request.tenant, ("product", "cashier", "customer", "payment_method"))
    context = _base_context(request, form, "Sales report", "reports:sales-export")
    if form.is_valid():
        data = sales_report_data(request.tenant, form)
        data["details"], context["query_string"] = _page(data["details"], request)
        context.update(data)
    return _render(request, "reports/sales.html", context)


@login_required
@tenant_role_required(*REPORT_ROLES, action_name="export sales reports")
def sales_export(request):
    form = _form(request, request.tenant, ("product", "cashier", "customer", "payment_method"))
    if not form.is_valid():
        return HttpResponse("Invalid report filters.", status=400)
    response, writer = _csv_response("sales_report", form, ["Date/time", "Sale", "Invoice", "Customer", "Cashier", "Items", "Total", "Payment method"])
    rows = sales_report_data(request.tenant, form)["details"]
    for sale in rows.iterator(chunk_size=1000):
        writer.writerow([_local_iso(sale.created_at, request.tenant), _safe(sale.sale_number), _safe(sale.invoice.invoice_number), _safe(sale.customer or "Walk-in customer"), _safe(sale.cashier.get_full_name() or sale.cashier.get_username()), sale.item_count or 0, sale.grand_total, sale.payment.get_method_display()])
    return response


@login_required
@tenant_role_required(*REPORT_ROLES, action_name="view purchase reports")
def purchase_report(request):
    form = _form(request, request.tenant, ("supplier", "product"))
    context = _base_context(request, form, "Purchases report", "reports:purchases-export")
    if form.is_valid():
        data = purchase_report_data(request.tenant, form)
        data["details"], context["query_string"] = _page(data["details"], request)
        context.update(data)
    return _render(request, "reports/purchases.html", context)


@login_required
@tenant_role_required(*REPORT_ROLES, action_name="export purchase reports")
def purchase_export(request):
    form = _form(request, request.tenant, ("supplier", "product"))
    if not form.is_valid():
        return HttpResponse("Invalid report filters.", status=400)
    response, writer = _csv_response("purchases_report", form, ["Received date", "Purchase", "Supplier", "Units received", "Total cost", "Received by"])
    for purchase in purchase_report_data(request.tenant, form)["details"].iterator(chunk_size=1000):
        writer.writerow([_local_iso(purchase.received_date, request.tenant), _safe(purchase.purchase_number), _safe(purchase.supplier.name), purchase.item_count or 0, purchase.total_cost or 0, _safe(purchase.received_by.get_full_name() or purchase.received_by.get_username())])
    return response


def _inventory_view(request, *, alerts_only=False):
    form = _form(request, request.tenant, ("category", "stock_status"))
    name = "Low-stock / out-of-stock report" if alerts_only else "Inventory report"
    export = "reports:low-stock-export" if alerts_only else "reports:inventory-export"
    context = _base_context(request, form, name, export)
    if form.is_valid():
        products = inventory_products(request.tenant, form, alerts_only=alerts_only)
        context["products"], context["query_string"] = _page(products, request)
        context["summary"] = inventory_summary(request.tenant)
        context["valuation_available"] = False
        context["alerts_only"] = alerts_only
    return _render(request, "reports/low_stock.html" if alerts_only else "reports/inventory.html", context)


@login_required
@tenant_role_required(*REPORT_ROLES, action_name="view inventory reports")
def inventory_report(request):
    return _inventory_view(request)


@login_required
@tenant_role_required(*REPORT_ROLES, action_name="view low-stock reports")
def low_stock_report(request):
    return _inventory_view(request, alerts_only=True)


def _inventory_export(request, *, alerts_only=False):
    form = _form(request, request.tenant, ("category", "stock_status"))
    if not form.is_valid():
        return HttpResponse("Invalid report filters.", status=400)
    slug = "low_stock_report" if alerts_only else "inventory_report"
    headers = ["Product", "SKU", "Category", "Current stock", "Reorder level", "Stock status"]
    if alerts_only:
        headers.append("Last stock movement")
    response, writer = _csv_response(slug, form, headers)
    for product in inventory_products(request.tenant, form, alerts_only=alerts_only).iterator(chunk_size=1000):
        status = "Out of stock" if product.current_stock == 0 else ("Low stock" if product.current_stock <= product.reorder_level else "In stock")
        row = [_safe(product.name), _safe(product.sku), _safe(product.category.name if product.category else "Uncategorized"), product.current_stock, product.reorder_level, status]
        if alerts_only:
            row.append(_local_iso(product.last_movement_at, request.tenant) if product.last_movement_at else "")
        writer.writerow(row)
    return response


@login_required
@tenant_role_required(*REPORT_ROLES, action_name="export inventory reports")
def inventory_export(request):
    return _inventory_export(request)


@login_required
@tenant_role_required(*REPORT_ROLES, action_name="export low-stock reports")
def low_stock_export(request):
    return _inventory_export(request, alerts_only=True)


def _attach_profit(rows, tenant, form):
    profit = profit_data(tenant, form)
    if not profit["available"]:
        return False
    cost_expr = ExpressionWrapper(F("quantity") * F("unit_cost_snapshot"), output_field=DecimalField(max_digits=18, decimal_places=2))
    costs = {row["product_id"]: row for row in sale_items(tenant, form).values("product_id").annotate(cogs=Sum(cost_expr), revenue=Sum("line_total"))}
    for product in rows:
        cost = costs.get(product.pk)
        product.gross_profit_estimate = (cost["revenue"] - cost["cogs"]) if cost else Decimal("0.00")
    return True


@login_required
@tenant_role_required(*REPORT_ROLES, action_name="view product performance reports")
def product_performance_report(request):
    form = _form(request, request.tenant, ("category", "product"))
    context = _base_context(request, form, "Product performance report", "reports:products-export")
    if form.is_valid():
        page, context["query_string"] = _page(product_performance_data(request.tenant, form), request)
        context["profit_available"] = _attach_profit(page.object_list, request.tenant, form)
        context["products"] = page
    return _render(request, "reports/products.html", context)


@login_required
@tenant_role_required(*REPORT_ROLES, action_name="export product performance reports")
def product_performance_export(request):
    form = _form(request, request.tenant, ("category", "product"))
    if not form.is_valid():
        return HttpResponse("Invalid report filters.", status=400)
    products = list(product_performance_data(request.tenant, form))
    available = _attach_profit(products, request.tenant, form)
    headers = ["Product", "SKU", "Category", "Quantity sold", "Sales revenue", "Current stock", "Stock status"]
    if available:
        headers.append("Gross profit estimate")
    response, writer = _csv_response("product_performance_report", form, headers)
    for product in products:
        status = "Service" if not product.track_inventory else ("Out of stock" if product.current_stock == 0 else ("Low stock" if product.current_stock <= product.reorder_level else "In stock"))
        row = [_safe(product.name), _safe(product.sku), _safe(product.category.name if product.category else "Uncategorized"), product.quantity_sold, product.sales_revenue, product.current_stock if product.track_inventory else "", status]
        if available:
            row.append(product.gross_profit_estimate)
        writer.writerow(row)
    return response


@login_required
@tenant_role_required(*REPORT_ROLES, action_name="view profit reports")
def profit_report(request):
    form = _form(request, request.tenant)
    context = _base_context(request, form, "Gross profit estimate", "reports:profit-export")
    if form.is_valid():
        context.update(profit_data(request.tenant, form))
        if context.get("available"):
            context["margin_display"] = f"{context['margin']:.2f}%"
        else:
            context["export_url"] = None
    return _render(request, "reports/profit.html", context)


@login_required
@tenant_role_required(*REPORT_ROLES, action_name="export profit reports")
def profit_export(request):
    form = _form(request, request.tenant)
    if not form.is_valid():
        return HttpResponse("Invalid report filters.", status=400)
    data = profit_data(request.tenant, form)
    if not data["available"]:
        return HttpResponse("Historical cost data is unavailable for this range.", status=409)
    response, writer = _csv_response("gross_profit_estimate", form, ["Product", "SKU", "Category", "Quantity sold", "Revenue", "Historical COGS", "Gross profit estimate"])
    for row in data["products"].iterator(chunk_size=1000):
        writer.writerow([_safe(row["product__name"]), _safe(row["product__sku"]), _safe(row["product__category__name"] or "Uncategorized"), row["quantity_sold"], row["revenue"], row["cogs"], row["gross_profit"]])
    return response
