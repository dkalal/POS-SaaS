from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from catalog.models import Product
from inventory.models import StockMovement
from payments.models import Payment
from purchasing.models import Purchase, PurchaseItem
from sales.models import Sale, SaleItem


ZERO = Decimal("0.00")
MONEY_FIELD = DecimalField(max_digits=18, decimal_places=2)


def completed_sales(tenant, form):
    start, end = form.datetime_bounds()
    qs = Sale.objects.filter(
        tenant=tenant,
        status=Sale.Status.COMPLETED,
        payment__status=Payment.Status.COMPLETED,
        created_at__gte=start,
        created_at__lt=end,
    )
    data = form.cleaned_data
    if data.get("customer"):
        qs = qs.filter(customer=data["customer"])
    if data.get("cashier"):
        qs = qs.filter(cashier=data["cashier"].user)
    if data.get("payment_method"):
        qs = qs.filter(payment__method=data["payment_method"])
    if data.get("product"):
        item_sale_ids = SaleItem.objects.filter(
            tenant=tenant, product=data["product"], sale__tenant=tenant
        ).values("sale_id")
        qs = qs.filter(pk__in=item_sale_ids)
    return qs


def sale_items(tenant, form):
    qs = SaleItem.objects.filter(tenant=tenant, sale_id__in=completed_sales(tenant, form).values("pk"))
    if form.cleaned_data.get("product"):
        qs = qs.filter(product=form.cleaned_data["product"])
    if form.cleaned_data.get("category"):
        qs = qs.filter(product__category=form.cleaned_data["category"])
    return qs


def sales_report_data(tenant, form):
    sales = completed_sales(tenant, form)
    items = sale_items(tenant, form)
    summary = sales.aggregate(total=Coalesce(Sum("grand_total"), Value(ZERO), output_field=MONEY_FIELD), count=Count("id"))
    item_total = items.aggregate(total=Coalesce(Sum("quantity"), Value(0), output_field=IntegerField()))["total"]
    count = summary["count"]
    summary["average"] = (summary["total"] / count).quantize(Decimal("0.01")) if count else ZERO
    summary["items"] = item_total
    top_products = (
        items.values("product_id", "product__name", "product__sku")
        .annotate(quantity_sold=Sum("quantity"), revenue=Sum("line_total"))
        .order_by("-quantity_sold", "product__name")[:20]
    )
    by_cashier = (
        sales.values("cashier_id", "cashier__username", "cashier__first_name", "cashier__last_name")
        .annotate(sale_count=Count("id"), total=Sum("grand_total"))
        .order_by("-total")
    )
    by_payment = list(sales.values("payment__method").annotate(sale_count=Count("id"), total=Sum("grand_total")).order_by("-total"))
    method_labels = dict(Payment.Method.choices)
    for row in by_payment:
        row["method_label"] = method_labels.get(row["payment__method"], row["payment__method"])
    details = sales.select_related("customer", "cashier", "payment", "invoice").annotate(item_count=Sum("items__quantity")).order_by("-created_at", "-id")
    return {"summary": summary, "top_products": top_products, "by_cashier": by_cashier, "by_payment": by_payment, "details": details}


def received_purchases(tenant, form):
    start, end = form.datetime_bounds()
    qs = Purchase.objects.filter(tenant=tenant, status=Purchase.Status.RECEIVED, received_date__gte=start, received_date__lt=end)
    if form.cleaned_data.get("supplier"):
        qs = qs.filter(supplier=form.cleaned_data["supplier"])
    if form.cleaned_data.get("product"):
        ids = PurchaseItem.objects.filter(tenant=tenant, product=form.cleaned_data["product"]).values("purchase_id")
        qs = qs.filter(pk__in=ids)
    return qs


def purchase_report_data(tenant, form):
    purchases = received_purchases(tenant, form)
    items = PurchaseItem.objects.filter(tenant=tenant, purchase_id__in=purchases.values("pk"))
    if form.cleaned_data.get("product"):
        items = items.filter(product=form.cleaned_data["product"])
    totals = items.aggregate(spend=Coalesce(Sum("line_total"), Value(ZERO), output_field=MONEY_FIELD), units=Coalesce(Sum("quantity"), Value(0), output_field=IntegerField()))
    totals["count"] = purchases.count()
    by_supplier = (
        PurchaseItem.objects.filter(tenant=tenant, purchase_id__in=purchases.values("pk"))
        .values("purchase__supplier_id", "purchase__supplier__name")
        .annotate(purchase_count=Count("purchase_id", distinct=True), amount=Sum("line_total"))
        .order_by("-amount")
    )
    details = purchases.select_related("supplier", "received_by").annotate(item_count=Sum("items__quantity"), total_cost=Sum("items__line_total")).order_by("-received_date", "-id")
    return {"summary": totals, "by_supplier": by_supplier, "details": details}


def inventory_products(tenant, form=None, alerts_only=False):
    last_movement = StockMovement.objects.filter(tenant=tenant, product_id=OuterRef("pk")).order_by("-created_at", "-id")
    qs = Product.objects.filter(tenant=tenant, track_inventory=True).select_related("category", "stock").annotate(
        current_stock=Coalesce("stock__quantity", Value(0), output_field=IntegerField()),
        last_movement_at=Subquery(last_movement.values("created_at")[:1]),
    )
    if form and form.cleaned_data.get("category"):
        qs = qs.filter(category=form.cleaned_data["category"])
    status = form.cleaned_data.get("stock_status") if form else ""
    if alerts_only and not status:
        qs = qs.filter(current_stock__lte=F("reorder_level"))
    elif status == "in_stock":
        qs = qs.filter(current_stock__gt=F("reorder_level"))
    elif status == "low_stock":
        qs = qs.filter(current_stock__gt=0, current_stock__lte=F("reorder_level"))
    elif status == "out_of_stock":
        qs = qs.filter(current_stock=0)
    return qs.order_by("current_stock", "name")


def inventory_summary(tenant):
    qs = inventory_products(tenant)
    return qs.aggregate(
        total=Count("id"),
        in_stock=Count("id", filter=Q(current_stock__gt=F("reorder_level"))),
        low_stock=Count("id", filter=Q(current_stock__gt=0, current_stock__lte=F("reorder_level"))),
        out_of_stock=Count("id", filter=Q(current_stock=0)),
    )


def profit_data(tenant, form):
    sales = completed_sales(tenant, form)
    items = sale_items(tenant, form)
    item_count = items.count()
    invalid_cost_count = items.filter(unit_cost_snapshot__lte=0).count()
    available = item_count > 0 and invalid_cost_count == 0
    if not available:
        return {"available": False, "item_count": item_count, "invalid_cost_count": invalid_cost_count, "products": []}
    cost_expr = ExpressionWrapper(F("quantity") * F("unit_cost_snapshot"), output_field=MONEY_FIELD)
    cogs = items.aggregate(total=Coalesce(Sum(cost_expr), Value(ZERO), output_field=MONEY_FIELD))["total"]
    revenue = sales.aggregate(total=Coalesce(Sum("grand_total"), Value(ZERO), output_field=MONEY_FIELD))["total"]
    gross_profit = revenue - cogs
    margin = (gross_profit / revenue * Decimal("100")).quantize(Decimal("0.01")) if revenue else ZERO
    products = (
        items.values("product_id", "product__name", "product__sku", "product__category__name")
        .annotate(quantity_sold=Sum("quantity"), revenue=Sum("line_total"), cogs=Sum(cost_expr))
        .annotate(gross_profit=ExpressionWrapper(F("revenue") - F("cogs"), output_field=MONEY_FIELD))
        .order_by("-gross_profit", "product__name")
    )
    return {"available": True, "revenue": revenue, "cogs": cogs, "gross_profit": gross_profit, "margin": margin, "products": products}


def product_performance_data(tenant, form):
    items = sale_items(tenant, form)
    products = Product.objects.filter(tenant=tenant)
    if form.cleaned_data.get("category"):
        products = products.filter(category=form.cleaned_data["category"])
    if form.cleaned_data.get("product"):
        products = products.filter(pk=form.cleaned_data["product"].pk)
    item_rows = items.values("product_id").annotate(quantity=Sum("quantity"), revenue=Sum("line_total"))
    quantity_sq = item_rows.filter(product_id=OuterRef("pk")).values("quantity")[:1]
    revenue_sq = item_rows.filter(product_id=OuterRef("pk")).values("revenue")[:1]
    return products.select_related("category", "stock").annotate(
        quantity_sold=Coalesce(Subquery(quantity_sq), Value(0), output_field=IntegerField()),
        sales_revenue=Coalesce(Subquery(revenue_sq), Value(ZERO), output_field=MONEY_FIELD),
        current_stock=Coalesce("stock__quantity", Value(0), output_field=IntegerField()),
    ).order_by("-quantity_sold", "name")
