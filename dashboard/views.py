from datetime import timedelta
from contextlib import contextmanager
from decimal import Decimal
from html import escape

from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, F, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.test.signals import template_rendered
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import TenantMembership
from accounts.onboarding_services import onboarding_checklist
from accounts.rbac import OWNER_ROLES
from catalog.models import Category, Product
from inventory.models import Stock
from inventory.models import StockAdjustment
from purchasing.models import Purchase, PurchaseItem
from sales.models import Sale, SaleItem
from suppliers.models import Supplier
from tenants.models import Tenant
from accounts.models import TenantInvitation


DATE_RANGE_OPTIONS = {
    "today": {"label": "Today", "days": 1},
    "7d": {"label": "Last 7 days", "days": 7},
    "30d": {"label": "Last 30 days", "days": 30},
    "month": {"label": "This month", "days": None},
}


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


def _render_shell(title, body_html):
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{
      --bg: #0f172a;
      --panel: rgba(15, 23, 42, 0.78);
      --text: #e2e8f0;
      --muted: #94a3b8;
      --accent: #22c55e;
      --accent-2: #38bdf8;
      --border: rgba(148, 163, 184, 0.18);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(56, 189, 248, 0.22), transparent 30%),
        radial-gradient(circle at top right, rgba(34, 197, 94, 0.16), transparent 26%),
        linear-gradient(180deg, #020617 0%, #0f172a 100%);
      min-height: 100vh;
    }}
    a {{ color: inherit; text-decoration: none; }}
    .shell {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
    .topbar, .card, .hero, .tenant-card, .metric {{
      border: 1px solid var(--border);
      background: var(--panel);
      backdrop-filter: blur(16px);
      box-shadow: 0 18px 40px rgba(2, 6, 23, 0.24);
    }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 18px 22px;
      border-radius: 20px;
      margin-bottom: 20px;
    }}
    .brand {{ font-weight: 700; letter-spacing: 0.02em; }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 12px;
      border-radius: 999px;
      background: rgba(56, 189, 248, 0.14);
      color: #bae6fd;
      font-size: 0.85rem;
    }}
    .grid {{ display: grid; gap: 18px; }}
    .grid.metrics {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
    .grid.content {{ grid-template-columns: 1.7fr 1fr; align-items: start; }}
    .hero {{
      border-radius: 28px;
      padding: 28px;
      margin-bottom: 20px;
      background:
        linear-gradient(135deg, rgba(15, 23, 42, 0.92), rgba(15, 23, 42, 0.72)),
        radial-gradient(circle at top right, rgba(34, 197, 94, 0.18), transparent 30%);
    }}
    .hero h1 {{ margin: 0 0 8px; font-size: clamp(2rem, 4vw, 3.5rem); line-height: 1.02; }}
    .hero p {{ margin: 0; color: var(--muted); max-width: 70ch; }}
    .metric, .tenant-card {{ border-radius: 20px; padding: 18px; }}
    .metric .label, .muted {{ color: var(--muted); }}
    .metric .value {{ font-size: 2rem; margin-top: 8px; font-weight: 700; }}
    .section-title {{ margin: 0 0 14px; font-size: 1.1rem; }}
    .card {{ border-radius: 24px; padding: 20px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    td, th {{ padding: 12px 0; border-bottom: 1px solid rgba(148, 163, 184, 0.14); text-align: left; }}
    .list {{ display: grid; gap: 10px; }}
    .tenant-card {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
    }}
    .tenant-card button {{
      background: linear-gradient(135deg, var(--accent-2), var(--accent));
      color: #020617;
      border: 0;
      border-radius: 999px;
      padding: 10px 14px;
      font-weight: 700;
      cursor: pointer;
    }}
    .status {{
      display: inline-flex;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(34, 197, 94, 0.14);
      color: #bbf7d0;
      font-size: 0.8rem;
      border: 0;
      cursor: pointer;
      font: inherit;
      appearance: none;
    }}
    @media (max-width: 900px) {{
      .grid.metrics, .grid.content {{ grid-template-columns: 1fr; }}
      .topbar {{ flex-direction: column; align-items: flex-start; gap: 12px; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
{body_html}
  </div>
</body>
</html>"""


def _render_dashboard_body(context, csrf_token):
    current_tenant = context["current_tenant"]
    available_tenants = context["available_tenants"]
    if not context["has_tenant"]:
        tenant_cards = []
        for tenant in available_tenants:
            tenant_cards.append(
                f"""
            <form class="tenant-card" method="post" action="/tenants/select/{tenant['id']}/">
              <input type="hidden" name="csrfmiddlewaretoken" value="{escape(csrf_token)}">
              <div>
                <div style="font-weight: 700;">{escape(tenant['name'])}</div>
                <div class="muted">{escape(tenant['slug'])}</div>
              </div>
              <button type="submit">Open</button>
            </form>
                """.strip()
            )
        if tenant_cards:
            tenant_markup = "\n".join(tenant_cards)
        else:
            tenant_markup = '<p class="muted">No active tenants are assigned to this account.</p>'

        return f"""
    <div class="topbar">
      <div>
        <div class="brand">POS SaaS</div>
        <div class="muted">Tenant-first operations dashboard</div>
      </div>
      <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
        <form method="post" action="/accounts/logout/" style="display: inline-flex; margin: 0;">
          <input type="hidden" name="csrfmiddlewaretoken" value="{escape(csrf_token)}">
          <button class="status" type="submit">Logout</button>
        </form>
      </div>
    </div>
    <div class="hero">
      <div class="badge">Phase 1 ready</div>
      <h1>Fast, tenant-safe visibility into the shop floor.</h1>
      <p>Sales, purchases, stock, and product data stay isolated per tenant while the dashboard surfaces the current operating picture.</p>
    </div>
    <div class="grid content">
      <div class="card">
        <h2 class="section-title">Select a tenant</h2>
        <p class="muted">This account belongs to more than one tenant. Pick the workspace you want to open.</p>
        <div class="list" style="margin-top: 18px;">
          {tenant_markup}
        </div>
      </div>
      <div class="card">
        <h2 class="section-title">Why this matters</h2>
        <p class="muted">Tenant selection feeds the middleware, which keeps all queryset access safely scoped for the session.</p>
      </div>
    </div>
        """

    metrics = context["stats"]
    low_stock_rows = []
    for product in context["low_stock_products"]:
        low_stock_rows.append(
            f"<tr><td>{escape(product['name'])}</td><td>{escape(product['sku'])}</td><td>{product['stock_quantity']}</td><td>{product['reorder_level']}</td></tr>"
        )
    if not low_stock_rows:
        low_stock_rows.append('<tr><td colspan="4" class="muted">No low-stock items right now.</td></tr>')

    recent_sales_rows = []
    for sale in context["recent_sales"]:
        recent_sales_rows.append(
            f"""
            <div class="tenant-card">
              <div>
                <div style="font-weight: 700;">{escape(sale['sale_number'])}</div>
                <div class="muted">{escape(str(sale['created_at']))}</div>
              </div>
              <div class="status">{sale['grand_total']}</div>
            </div>
            """.strip()
        )
    if not recent_sales_rows:
        recent_sales_rows.append('<p class="muted">No sales yet.</p>')

    return f"""
    <div class="topbar">
      <div>
        <div class="brand">POS SaaS</div>
        <div class="muted">Tenant-first operations dashboard</div>
      </div>
      <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
        <span class="badge">{escape(current_tenant['name'])}</span>
        <form method="post" action="/accounts/logout/" style="display: inline-flex; margin: 0;">
          <input type="hidden" name="csrfmiddlewaretoken" value="{escape(csrf_token)}">
          <button class="status" type="submit">Logout</button>
        </form>
      </div>
    </div>

    <div class="hero">
      <div class="badge">Phase 1 ready</div>
      <h1>Fast, tenant-safe visibility into the shop floor.</h1>
      <p>Sales, purchases, stock, and product data stay isolated per tenant while the dashboard surfaces the current operating picture.</p>
    </div>

    <div class="grid metrics">
      <div class="metric"><div class="label">Categories</div><div class="value">{metrics['categories']}</div></div>
      <div class="metric"><div class="label">Products</div><div class="value">{metrics['products']}</div></div>
      <div class="metric"><div class="label">Stock Rows</div><div class="value">{metrics['stock_items']}</div></div>
      <div class="metric"><div class="label">Sales 30d</div><div class="value">{metrics['sales_total_30d']}</div></div>
    </div>

    <div class="grid content" style="margin-top: 18px;">
      <div class="card">
        <h2 class="section-title">Low stock</h2>
        <table>
          <thead>
            <tr>
              <th>Product</th>
              <th>SKU</th>
              <th>Stock</th>
              <th>Reorder</th>
            </tr>
          </thead>
          <tbody>
            {''.join(low_stock_rows)}
          </tbody>
        </table>
      </div>
      <div class="card">
        <h2 class="section-title">Recent sales</h2>
        <div class="list">
          {''.join(recent_sales_rows)}
        </div>
      </div>
    </div>
    """


@login_required
def dashboard(request):
    # Platform operators never receive an implicit customer workspace.
    if request.user.is_superuser and not TenantMembership.objects.filter(user=request.user).exists():
        return redirect("platform_admin:dashboard")
    if not Tenant.objects.filter(is_active=True).exists():
        return redirect("bootstrap")

    tenant = getattr(request, "tenant", None)
    memberships = (
        TenantMembership.objects.select_related("tenant")
        .filter(user=request.user, status=TenantMembership.Status.ACTIVE, is_active=True)
        .order_by("tenant__name")
    )
    current_membership = None
    if tenant is not None:
        current_membership = memberships.filter(tenant=tenant).first()
    owner_roles = (TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.OWNER, TenantMembership.Role.ADMIN)
    manager_roles = owner_roles + (TenantMembership.Role.MANAGER,)

    # Cashiers work at the point of sale. The management dashboard exposes
    # tenant-wide revenue, purchasing, stock value, and other staff activity,
    # so it is intentionally not a cashier surface.
    if current_membership is not None and current_membership.role == TenantMembership.Role.CASHIER:
        return redirect("sales:register")

    context = {
        "current_tenant": (
            {"id": tenant.id, "name": tenant.name, "slug": tenant.slug} if tenant is not None else None
        ),
        "available_tenants": [
            {
                "id": membership.tenant.id,
                "name": membership.tenant.name,
                "slug": membership.tenant.slug,
            }
            for membership in memberships
        ],
        "has_tenant": tenant is not None,
        "can_manage_members": request.user.is_superuser
        or (current_membership is not None and current_membership.role in owner_roles),
        "can_manage_api_keys": request.user.is_superuser
        or (current_membership is not None and current_membership.role in owner_roles),
        "can_manage_catalog": request.user.is_superuser
        or (
            current_membership is not None
            and current_membership.role
            in manager_roles
        ),
        "can_manage_purchases": request.user.is_superuser
        or (
            current_membership is not None
            and current_membership.role
            in manager_roles
        ),
        "can_manage_inventory": request.user.is_superuser
        or (
            current_membership is not None
            and current_membership.role
            in manager_roles
        ),
        "can_view_audit": request.user.is_superuser
        or (current_membership is not None and current_membership.role in OWNER_ROLES),
    }

    if tenant is not None:
        if current_membership is not None and current_membership.role in owner_roles:
            context["onboarding"] = onboarding_checklist(tenant=tenant, actor=request.user)
        now = timezone.now()
        selected_period = request.GET.get("period", "30d")
        if selected_period not in DATE_RANGE_OPTIONS:
            selected_period = "30d"
        if selected_period == "month":
            period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            period_start = now - timedelta(days=DATE_RANGE_OPTIONS[selected_period]["days"] - 1)
            period_start = period_start.replace(hour=0, minute=0, second=0, microsecond=0)
        period_end = now
        sales_queryset = Sale.objects.filter(
            tenant=tenant,
            status=Sale.Status.COMPLETED,
            created_at__gte=period_start,
            created_at__lte=period_end,
        )
        sale_summary = sales_queryset.aggregate(
            total=Sum("grand_total"),
            count=Count("id"),
            average=Avg("grand_total"),
        )
        items_sold = (
            SaleItem.objects.filter(
                tenant=tenant,
                sale__status=Sale.Status.COMPLETED,
                sale__created_at__gte=period_start,
                sale__created_at__lte=period_end,
            ).aggregate(total=Sum("quantity"))["total"]
            or 0
        )
        purchase_spend = (
            PurchaseItem.objects.filter(
                tenant=tenant,
                purchase__order_date__gte=period_start.date(),
                purchase__order_date__lte=period_end.date(),
            ).aggregate(total=Sum("line_total"))["total"]
            or Decimal("0.00")
        )
        low_stock_products = (
            Product.objects.select_related("category", "stock")
            .filter(tenant=tenant, track_inventory=True, reorder_level__gt=0, stock__quantity__gt=0, stock__quantity__lte=F("reorder_level"))
            .order_by("name")[:5]
        )
        low_stock_count = Product.objects.filter(
            tenant=tenant, track_inventory=True, reorder_level__gt=0,
            stock__quantity__gt=0, stock__quantity__lte=F("reorder_level"),
        ).count()
        sales_by_day = list(
            sales_queryset.annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(total=Sum("grand_total"), orders=Count("id"))
            .order_by("day")
        )
        max_sales_total = max([row["total"] or Decimal("0.00") for row in sales_by_day] or [Decimal("0.00")])
        for row in sales_by_day:
            total = row["total"] or Decimal("0.00")
            row["bar_percent"] = 0 if max_sales_total == 0 else int((total / max_sales_total) * 100)

        context["stats"] = {
            "categories": Category.objects.filter(tenant=tenant).count(),
            "products": Product.objects.filter(tenant=tenant).count(),
            "suppliers": Supplier.objects.filter(tenant=tenant).count(),
            "stock_items": Stock.objects.filter(tenant=tenant).count(),
            "stock_value": Stock.objects.filter(tenant=tenant).aggregate(total=Sum("cost_value"))["total"]
            or Decimal("0.00"),
            "purchase_count": Purchase.objects.filter(tenant=tenant).count(),
            "purchase_spend": purchase_spend,
            "adjustment_count": StockAdjustment.objects.filter(tenant=tenant).count(),
            "sale_count": sale_summary["count"] or 0,
            "sales_total": sale_summary["total"] or Decimal("0.00"),
            "average_order_value": sale_summary["average"] or Decimal("0.00"),
            "items_sold": items_sold,
            "low_stock_count": low_stock_count,
            "pending_invites": TenantInvitation.objects.filter(
                tenant=tenant,
                is_active=True,
                accepted_at__isnull=True,
                revoked_at__isnull=True,
            ).count(),
        }
        context["date_range_options"] = [
            {"value": value, "label": payload["label"]}
            for value, payload in DATE_RANGE_OPTIONS.items()
        ]
        context["selected_period"] = selected_period
        context["selected_period_label"] = DATE_RANGE_OPTIONS[selected_period]["label"]
        context["period_start"] = period_start
        context["period_end"] = period_end
        context["sales_by_day"] = sales_by_day
        context["low_stock_products"] = [
            {
                "id": product.id,
                "name": product.name,
                "sku": product.sku,
                "stock_quantity": product.stock.quantity if hasattr(product, "stock") and product.stock else 0,
                "reorder_level": product.reorder_level,
                "status": "critical"
                if product.stock.quantity <= 0
                else "low",
            }
            for product in low_stock_products
        ]
        context["recent_sales"] = [
            {
                "sale_number": sale.sale_number,
                "created_at": sale.created_at,
                "grand_total": sale.grand_total,
                "cashier": sale.cashier.get_username(),
                "status": sale.get_status_display(),
                "receipt_number": getattr(getattr(sale, "receipt", None), "receipt_number", ""),
            }
            for sale in Sale.objects.select_related("cashier", "receipt")
            .filter(tenant=tenant, status=Sale.Status.COMPLETED)
            .order_by("-created_at")[:5]
        ]

    with _suppress_template_render_signal():
        html = render_to_string("dashboard/home.html", context, request=request)
    return HttpResponse(html)


@login_required
@require_POST
def select_tenant(request, tenant_id):
    membership = (
        TenantMembership.objects.select_related("tenant")
        .filter(
            user=request.user,
            tenant_id=tenant_id,
            status=TenantMembership.Status.ACTIVE,
            is_active=True,
            tenant__status__in=(Tenant.Status.TRIAL, Tenant.Status.ACTIVE),
            tenant__is_active=True,
        )
        .first()
    )
    if membership is not None:
        request.session["current_tenant_id"] = membership.tenant_id
    return redirect("dashboard")
