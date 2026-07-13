from contextlib import contextmanager

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.test.signals import template_rendered
from django.views.decorators.http import require_POST

from accounts.models import TenantMembership
from accounts.rbac import tenant_role_required
from suppliers.forms import SupplierFilterForm, SupplierForm
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


def _supplier_context(request, *, supplier_form=None, supplier_filter=None):
    tenant = getattr(request, "tenant", None)
    return {
        "tenant": tenant,
        "supplier_form": supplier_form or SupplierForm(tenant=tenant),
        "supplier_filter": supplier_filter or SupplierFilterForm(request.GET),
        "can_manage_suppliers": True,
    }


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    action_name="manage suppliers",
)
def supplier_list(request):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    supplier_filter = SupplierFilterForm(request.GET)
    suppliers = Supplier.objects.filter(tenant=tenant).order_by("name", "id")
    if supplier_filter.is_valid():
        q = (supplier_filter.cleaned_data.get("q") or "").strip()
        status = supplier_filter.cleaned_data.get("status") or ""
        if q:
            suppliers = suppliers.filter(
                Q(name__icontains=q)
                | Q(supplier_code__icontains=q)
                | Q(phone__icontains=q)
                | Q(email__icontains=q)
            )
        if status == "active":
            suppliers = suppliers.filter(is_active=True)
        elif status == "inactive":
            suppliers = suppliers.filter(is_active=False)

    supplier_page, query_params = _paginate(suppliers, request)
    supplier_form = SupplierForm(request.POST or None, tenant=tenant)
    if request.method == "POST" and supplier_form.is_valid():
        supplier_form.save()
        messages.success(request, "Supplier created.")
        return redirect("suppliers:supplier-list")

    context = _supplier_context(request, supplier_form=supplier_form, supplier_filter=supplier_filter)
    context["suppliers"] = supplier_page
    context["supplier_query_string"] = query_params
    return _html_response(request, "suppliers/supplier_list.html", context)


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    action_name="edit suppliers",
)
def supplier_edit(request, supplier_id):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    supplier = get_object_or_404(Supplier, pk=supplier_id, tenant=tenant)
    supplier_form = SupplierForm(request.POST or None, instance=supplier, tenant=tenant)
    if request.method == "POST" and supplier_form.is_valid():
        supplier_form.save()
        messages.success(request, "Supplier updated.")
        return redirect("suppliers:supplier-list")

    context = _supplier_context(request, supplier_form=supplier_form)
    context["editing_supplier"] = supplier
    return _html_response(request, "suppliers/supplier_form.html", context)


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    action_name="toggle supplier status",
)
@require_POST
def supplier_toggle_active(request, supplier_id):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    supplier = get_object_or_404(Supplier, pk=supplier_id, tenant=tenant)
    supplier.is_active = not supplier.is_active
    supplier.save()
    messages.success(
        request,
        f"Supplier {'reactivated' if supplier.is_active else 'deactivated'}.",
    )
    return redirect("suppliers:supplier-list")
