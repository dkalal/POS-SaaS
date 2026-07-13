from contextlib import contextmanager

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.test.signals import template_rendered
from django.db.models import Q
from django.views.decorators.http import require_POST

from accounts.models import TenantMembership
from accounts.rbac import tenant_role_required
from catalog.forms import CatalogFilterForm, CategoryForm, ProductForm
from catalog.models import Category, Product


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
    query_params = _clean_querydict(request).urlencode()
    return page_obj, query_params


def _catalog_context(request, *, category_form=None, product_form=None, category_filter=None, product_filter=None):
    tenant = getattr(request, "tenant", None)
    return {
        "tenant": tenant,
        "category_form": category_form or CategoryForm(tenant=tenant),
        "product_form": product_form or ProductForm(tenant=tenant),
        "category_filter": category_filter or CatalogFilterForm(request.GET),
        "product_filter": product_filter or CatalogFilterForm(request.GET),
        "can_manage_catalog": True,
    }


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    action_name="manage categories",
)
def category_list(request):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    category_filter = CatalogFilterForm(request.GET)
    categories = Category.objects.filter(tenant=tenant).order_by("sort_order", "name", "id")
    if category_filter.is_valid():
        q = (category_filter.cleaned_data.get("q") or "").strip()
        status = category_filter.cleaned_data.get("status") or ""
        if q:
            categories = categories.filter(
                Q(name__icontains=q) | Q(slug__icontains=q) | Q(description__icontains=q)
            )
        if status == "active":
            categories = categories.filter(is_active=True)
        elif status == "inactive":
            categories = categories.filter(is_active=False)

    category_page, query_params = _paginate(categories, request)
    category_form = CategoryForm(request.POST or None, tenant=tenant)
    if request.method == "POST" and category_form.is_valid():
        category_form.save()
        messages.success(request, "Category created.")
        return redirect("catalog:category-list")

    context = _catalog_context(request, category_form=category_form, category_filter=category_filter)
    context["categories"] = category_page
    context["category_query_string"] = query_params
    return _html_response(request, "catalog/category_list.html", context)


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    action_name="edit categories",
)
def category_edit(request, category_id):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    category = get_object_or_404(Category, pk=category_id, tenant=tenant)
    category_form = CategoryForm(request.POST or None, instance=category, tenant=tenant)
    if request.method == "POST" and category_form.is_valid():
        category_form.save()
        messages.success(request, "Category updated.")
        return redirect("catalog:category-list")

    context = _catalog_context(request, category_form=category_form)
    context["editing_category"] = category
    return _html_response(request, "catalog/category_form.html", context)


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    action_name="toggle category status",
)
@require_POST
def category_toggle_active(request, category_id):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    category = get_object_or_404(Category, pk=category_id, tenant=tenant)
    category.is_active = not category.is_active
    category.save()
    messages.success(
        request,
        f"Category {'reactivated' if category.is_active else 'deactivated'}.",
    )
    return redirect("catalog:category-list")


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    action_name="manage products",
)
def product_list(request):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    product_filter = CatalogFilterForm(request.GET)
    products = Product.objects.select_related("category").filter(tenant=tenant).order_by("name", "id")
    if product_filter.is_valid():
        q = (product_filter.cleaned_data.get("q") or "").strip()
        status = product_filter.cleaned_data.get("status") or ""
        if q:
            products = products.filter(
                Q(name__icontains=q)
                | Q(sku__icontains=q)
                | Q(barcode__icontains=q)
                | Q(description__icontains=q)
                | Q(category__name__icontains=q)
            )
        if status == "active":
            products = products.filter(is_active=True)
        elif status == "inactive":
            products = products.filter(is_active=False)

    product_page, query_params = _paginate(products, request)
    product_form = ProductForm(request.POST or None, tenant=tenant)
    if request.method == "POST" and product_form.is_valid():
        product_form.save()
        messages.success(request, "Product created.")
        return redirect("catalog:product-list")

    context = _catalog_context(request, product_form=product_form, product_filter=product_filter)
    context["products"] = product_page
    context["product_query_string"] = query_params
    return _html_response(request, "catalog/product_list.html", context)


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    action_name="edit products",
)
def product_edit(request, product_id):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    product = get_object_or_404(Product.objects.select_related("category"), pk=product_id, tenant=tenant)
    product_form = ProductForm(request.POST or None, instance=product, tenant=tenant)
    if request.method == "POST" and product_form.is_valid():
        product_form.save()
        messages.success(request, "Product updated.")
        return redirect("catalog:product-list")

    context = _catalog_context(request, product_form=product_form)
    context["editing_product"] = product
    return _html_response(request, "catalog/product_form.html", context)


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    action_name="toggle product status",
)
@require_POST
def product_toggle_active(request, product_id):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    product = get_object_or_404(Product, pk=product_id, tenant=tenant)
    product.is_active = not product.is_active
    product.save()
    messages.success(
        request,
        f"Product {'reactivated' if product.is_active else 'deactivated'}.",
    )
    return redirect("catalog:product-list")
