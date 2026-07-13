from contextlib import contextmanager

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.test.signals import template_rendered

from accounts.models import TenantMembership
from accounts.rbac import tenant_role_required
from audit.forms import AuditFilterForm
from audit.models import AuditEvent


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


def _paginate(queryset, request, per_page=20):
    paginator = Paginator(queryset, per_page)
    page_obj = paginator.get_page(request.GET.get("page"))
    return page_obj, _clean_querydict(request).urlencode()


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    action_name="view audit log",
)
def audit_list(request):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    audit_filter = AuditFilterForm(request.GET)
    events = AuditEvent.objects.select_related("actor", "target_content_type").filter(tenant=tenant)
    if audit_filter.is_valid():
        q = (audit_filter.cleaned_data.get("q") or "").strip()
        action = audit_filter.cleaned_data.get("action") or ""
        if q:
            events = events.filter(
                Q(target_label__icontains=q)
                | Q(action__icontains=q)
                | Q(actor__username__icontains=q)
                | Q(actor__first_name__icontains=q)
                | Q(actor__last_name__icontains=q)
            )
        if action:
            events = events.filter(action=action)

    event_page, query_string = _paginate(events, request)
    return _html_response(
        request,
        "audit/audit_list.html",
        {
            "tenant": tenant,
            "events": event_page,
            "audit_filter": audit_filter,
            "audit_query_string": query_string,
        },
    )


@login_required
@tenant_role_required(
    TenantMembership.Role.OWNER_ADMIN,
    action_name="view audit details",
)
def audit_detail(request, event_id):
    tenant, response = _tenant_or_redirect(request)
    if response is not None:
        return response

    event = get_object_or_404(
        AuditEvent.objects.select_related("actor", "target_content_type"),
        pk=event_id,
        tenant=tenant,
    )
    return _html_response(
        request,
        "audit/audit_detail.html",
        {
            "tenant": tenant,
            "event": event,
        },
    )
