from contextlib import contextmanager
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.test.signals import template_rendered
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import TenantMembership
from platform_admin.forms import PlanChangeForm, PlanForm, TenantCreateForm, TrialExtensionForm
from platform_admin.models import PlatformAuditLog
from platform_admin.permissions import platform_admin_required
from platform_admin.services import change_plan, change_tenant_status, create_tenant, extend_trial
from tenants.models import SubscriptionPlan, Tenant


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
        return HttpResponse(render_to_string(template, context, request=request), status=status)


def _base_context(**extra):
    return {"platform_mode": True, **extra}


@login_required
@platform_admin_required
def dashboard(request):
    now = timezone.now()
    trials_cutoff = now + timedelta(days=7)
    tenants = Tenant.objects.all()
    context = _base_context(
        stats={
            "total_tenants": tenants.count(),
            "active_tenants": tenants.filter(status=Tenant.Status.ACTIVE).count(),
            "trials_expiring": tenants.filter(status=Tenant.Status.TRIAL, trial_ends_at__lte=trials_cutoff, trial_ends_at__gte=now).count(),
            "suspended_tenants": tenants.filter(status=Tenant.Status.SUSPENDED).count(),
            "active_users": TenantMembership.objects.filter(is_active=True, tenant__is_active=True).count(),
            "onboarding_complete": tenants.filter(onboarding__completed_at__isnull=False).count(),
        },
        recent_tenants=tenants.select_related("subscription_plan").order_by("-created_at")[:6],
        recent_audit=PlatformAuditLog.objects.select_related("actor", "target_tenant")[:8],
    )
    return _html_response(request, "platform_admin/dashboard.html", context)


@login_required
@platform_admin_required
def tenant_list(request):
    owner_memberships = TenantMembership.objects.filter(
        role__in=(TenantMembership.Role.OWNER, TenantMembership.Role.OWNER_ADMIN),
        status=TenantMembership.Status.ACTIVE,
        is_active=True,
    ).select_related("user")
    tenants = (
        Tenant.objects.select_related("subscription_plan", "onboarding")
        .prefetch_related(Prefetch("tenantmembership_set", queryset=owner_memberships, to_attr="active_owners"))
        .annotate(
            active_users=Count(
                "tenantmembership",
                filter=Q(tenantmembership__status=TenantMembership.Status.ACTIVE, tenantmembership__is_active=True),
                distinct=True,
            )
        )
        .order_by("-created_at", "name")
    )
    return _html_response(request, "platform_admin/tenant_list.html", _base_context(tenants=tenants))


@login_required
@platform_admin_required
def tenant_create(request):
    form = TenantCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        tenant = create_tenant(
            actor=request.user,
            plan=form.cleaned_data.pop("plan"),
            billing_cycle=form.cleaned_data.pop("billing_cycle"),
            **form.cleaned_data,
        )
        messages.success(request, f"{tenant.name} was created. Add its owner through the controlled team invitation flow.")
        return redirect("platform_admin:tenant-detail", tenant_id=tenant.pk)
    return _html_response(request, "platform_admin/form.html", _base_context(form=form, title="Create tenant", submit_label="Create tenant"), status=400 if request.method == "POST" else 200)


@login_required
@platform_admin_required
def tenant_detail(request, tenant_id):
    tenant = get_object_or_404(Tenant.objects.select_related("subscription_plan"), pk=tenant_id)
    memberships = TenantMembership.objects.filter(tenant=tenant).select_related("user").order_by("role", "user__username")
    return _html_response(
        request,
        "platform_admin/tenant_detail.html",
        _base_context(
            tenant=tenant,
            memberships=memberships,
            subscriptions=tenant.subscriptions.select_related("plan").all(),
            plan_form=PlanChangeForm(initial={"plan": tenant.subscription_plan, "billing_cycle": "monthly"}),
            trial_form=TrialExtensionForm(),
            audit_events=tenant.platform_audit_logs.select_related("actor")[:10],
        ),
    )


@login_required
@platform_admin_required
@require_POST
def tenant_status(request, tenant_id):
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    status = request.POST.get("status")
    try:
        change_tenant_status(
            tenant=tenant,
            status=status,
            actor=request.user,
            expected_status=request.POST.get("expected_status"),
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{tenant.name} is now {tenant.get_status_display().lower()}.")
    return redirect("platform_admin:tenant-detail", tenant_id=tenant.pk)


@login_required
@platform_admin_required
@require_POST
def tenant_trial_extend(request, tenant_id):
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    form = TrialExtensionForm(request.POST)
    if form.is_valid():
        extend_trial(tenant=tenant, days=form.cleaned_data["days"], actor=request.user)
        messages.success(request, "Trial extended and workspace access restored.")
    else:
        messages.error(request, "Enter a trial extension between 1 and 365 days.")
    return redirect("platform_admin:tenant-detail", tenant_id=tenant.pk)


@login_required
@platform_admin_required
@require_POST
def tenant_plan_change(request, tenant_id):
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    form = PlanChangeForm(request.POST)
    if form.is_valid():
        change_plan(tenant=tenant, plan=form.cleaned_data["plan"], billing_cycle=form.cleaned_data["billing_cycle"], actor=request.user)
        messages.success(request, "Subscription plan updated; history was retained.")
    else:
        messages.error(request, "Choose an active plan and billing cycle.")
    return redirect("platform_admin:tenant-detail", tenant_id=tenant.pk)


@login_required
@platform_admin_required
def plan_list(request):
    return _html_response(request, "platform_admin/plan_list.html", _base_context(plans=SubscriptionPlan.objects.annotate(subscription_count=Count("subscriptions"))))


@login_required
@platform_admin_required
def plan_create(request):
    form = PlanForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        plan = form.save()
        PlatformAuditLog.objects.create(actor=request.user, action=PlatformAuditLog.Action.PLAN_CREATED, after_data={"plan_id": plan.pk, "code": plan.code})
        messages.success(request, "Plan created.")
        return redirect("platform_admin:plan-list")
    return _html_response(request, "platform_admin/form.html", _base_context(form=form, title="Create subscription plan", submit_label="Create plan"), status=400 if request.method == "POST" else 200)


@login_required
@platform_admin_required
def plan_edit(request, plan_id):
    plan = get_object_or_404(SubscriptionPlan, pk=plan_id)
    form = PlanForm(request.POST or None, instance=plan)
    if request.method == "POST" and form.is_valid():
        before = {"name": plan.name, "is_active": plan.is_active}
        plan = form.save()
        action = PlatformAuditLog.Action.PLAN_UPDATED if plan.is_active else PlatformAuditLog.Action.PLAN_DISABLED
        PlatformAuditLog.objects.create(actor=request.user, action=action, before_data=before, after_data={"plan_id": plan.pk, "name": plan.name, "is_active": plan.is_active})
        messages.success(request, "Plan updated. Referenced plans remain preserved.")
        return redirect("platform_admin:plan-list")
    return _html_response(request, "platform_admin/form.html", _base_context(form=form, title=f"Edit {plan.name}", submit_label="Save plan"), status=400 if request.method == "POST" else 200)
