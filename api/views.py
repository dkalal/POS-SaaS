from contextlib import contextmanager

from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.test.signals import template_rendered
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from api.authentication import APIKeyAuthentication
from api.forms import APIKeyForm
from api.models import APIKey
from api.serializers import CategorySerializer, ProductSerializer, StockSerializer
from api.services import create_api_key, revoke_api_key
from api.throttles import ApiKeyRateThrottle
from accounts.models import TenantMembership
from accounts.rbac import tenant_role_required
from catalog.models import Category, Product
from inventory.models import Stock


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


class TenantScopedReadOnlyViewSet(viewsets.ReadOnlyModelViewSet):
    authentication_classes = [APIKeyAuthentication]
    throttle_classes = [ApiKeyRateThrottle]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if tenant is None:
            return self.queryset.none()
        return self.queryset.filter(tenant=tenant)


class CategoryViewSet(TenantScopedReadOnlyViewSet):
    queryset = Category.objects.select_related("tenant").all()
    serializer_class = CategorySerializer


class StockViewSet(TenantScopedReadOnlyViewSet):
    queryset = Stock.objects.select_related("product", "product__category").all()
    serializer_class = StockSerializer


class ProductViewSet(TenantScopedReadOnlyViewSet):
    queryset = Product.objects.select_related("category").all()
    serializer_class = ProductSerializer

    @action(detail=False, methods=["get"])
    def search(self, request):
        q = (request.query_params.get("q") or "").strip()
        if not q:
            return Response([])

        queryset = self.get_queryset()
        seen_ids = set()
        ordered_products = []

        sku_matches = list(queryset.filter(Q(sku__iexact=q) | Q(sku__icontains=q)).order_by("sku"))
        for product in sku_matches:
            if product.id not in seen_ids:
                ordered_products.append(product)
                seen_ids.add(product.id)

        name_matches = list(
            queryset.filter(name__icontains=q).exclude(id__in=seen_ids).order_by("name")
        )
        for product in name_matches:
            if product.id not in seen_ids:
                ordered_products.append(product)
                seen_ids.add(product.id)

        barcode_matches = list(
            queryset.filter(barcode__icontains=q).exclude(id__in=seen_ids).order_by("barcode")
        )
        for product in barcode_matches:
            if product.id not in seen_ids:
                ordered_products.append(product)
                seen_ids.add(product.id)

        serializer = self.get_serializer(ordered_products, many=True)
        return Response(serializer.data)


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _html_response(request, template, context, status=200):
    with _suppress_template_render_signal():
        html = render_to_string(template, context, request=request)
    return HttpResponse(html, status=status)


def _api_key_context(request, *, form=None, created_key=None, raw_key=None, api_keys=None):
    tenant = getattr(request, "tenant", None)
    if api_keys is None and tenant is not None:
        api_keys = APIKey.objects.filter(tenant=tenant).select_related("created_by").order_by("-created_at", "-id")
    return {
        "tenant": tenant,
        "form": form or APIKeyForm(),
        "api_keys": api_keys or APIKey.objects.none(),
        "created_key": created_key,
        "raw_key": raw_key,
        "can_manage_api_keys": True,
    }


@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, action_name="manage API keys")
def api_key_management(request):
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        return redirect("dashboard")

    context = _api_key_context(request)
    if request.method == "POST":
        form = APIKeyForm(request.POST)
        context["form"] = form
        if form.is_valid():
            created_key, raw_key = create_api_key(
                tenant=tenant,
                label=form.cleaned_data["label"],
                created_by=request.user,
                can_view_cost=form.cleaned_data["can_view_cost"],
                notes=form.cleaned_data["notes"],
            )
            context = _api_key_context(request, created_key=created_key, raw_key=raw_key)
            template = "api/partials/key_panel.html" if _is_htmx(request) else "api/key_management.html"
            return _html_response(request, template, context)
        template = "api/partials/key_panel.html" if _is_htmx(request) else "api/key_management.html"
        return _html_response(request, template, context, status=400)

    return _html_response(request, "api/key_management.html", context)


@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, action_name="revoke API keys")
def api_key_revoke(request, key_id):
    tenant = getattr(request, "tenant", None)
    api_key = get_object_or_404(APIKey, pk=key_id, tenant=tenant, is_active=True)
    revoke_api_key(api_key=api_key)
    context = _api_key_context(request)
    template = "api/partials/key_panel.html" if _is_htmx(request) else "api/key_management.html"
    return _html_response(request, template, context)
