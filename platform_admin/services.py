from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from platform_admin.models import PlatformAuditLog
from tenants.models import Tenant, TenantSubscription


def _tenant_snapshot(tenant):
    return {
        "status": tenant.status,
        "is_active": tenant.is_active,
        "trial_ends_at": tenant.trial_ends_at.isoformat() if tenant.trial_ends_at else None,
        "subscription_plan_id": tenant.subscription_plan_id,
    }


def _audit(*, actor, action, tenant=None, before_data=None, after_data=None, metadata=None):
    return PlatformAuditLog.objects.create(
        actor=actor,
        target_tenant=tenant,
        action=action,
        before_data=before_data or {},
        after_data=after_data or {},
        metadata=metadata or {},
    )


@transaction.atomic
def create_tenant(*, actor, plan, billing_cycle, **details):
    now = timezone.now()
    tenant = Tenant.objects.create(
        **details,
        subscription_plan=plan,
        status=Tenant.Status.TRIAL if plan.trial_days else Tenant.Status.ACTIVE,
        is_active=True,
        trial_ends_at=now + timedelta(days=plan.trial_days) if plan.trial_days else None,
    )
    TenantSubscription.objects.create(
        tenant=tenant,
        plan=plan,
        status=TenantSubscription.Status.TRIAL if plan.trial_days else TenantSubscription.Status.ACTIVE,
        billing_cycle=billing_cycle,
        started_at=now,
        current_period_ends_at=tenant.trial_ends_at,
    )
    _audit(actor=actor, action=PlatformAuditLog.Action.TENANT_CREATED, tenant=tenant, after_data=_tenant_snapshot(tenant))
    return tenant


@transaction.atomic
def change_tenant_status(*, tenant, status, actor):
    if status not in {Tenant.Status.ACTIVE, Tenant.Status.SUSPENDED, Tenant.Status.CANCELLED}:
        raise ValueError("Unsupported tenant status transition.")
    before_data = _tenant_snapshot(tenant)
    tenant.status = status
    tenant.is_active = status in {Tenant.Status.ACTIVE, Tenant.Status.TRIAL}
    tenant.save(update_fields=["status", "is_active", "updated_at"])
    action = {
        Tenant.Status.ACTIVE: PlatformAuditLog.Action.TENANT_ACTIVATED,
        Tenant.Status.SUSPENDED: PlatformAuditLog.Action.TENANT_SUSPENDED,
        Tenant.Status.CANCELLED: PlatformAuditLog.Action.TENANT_CANCELLED,
    }[status]
    _audit(actor=actor, action=action, tenant=tenant, before_data=before_data, after_data=_tenant_snapshot(tenant))
    return tenant


@transaction.atomic
def extend_trial(*, tenant, days, actor):
    if days <= 0:
        raise ValueError("Trial extension must be at least one day.")
    before_data = _tenant_snapshot(tenant)
    baseline = max(filter(None, [tenant.trial_ends_at, timezone.now()]))
    tenant.trial_ends_at = baseline + timedelta(days=days)
    tenant.status = Tenant.Status.TRIAL
    tenant.is_active = True
    tenant.save(update_fields=["trial_ends_at", "status", "is_active", "updated_at"])
    _audit(actor=actor, action=PlatformAuditLog.Action.TRIAL_EXTENDED, tenant=tenant, before_data=before_data, after_data=_tenant_snapshot(tenant), metadata={"days": days})
    return tenant


@transaction.atomic
def change_plan(*, tenant, plan, billing_cycle, actor):
    before_data = _tenant_snapshot(tenant)
    tenant.subscription_plan = plan
    tenant.save(update_fields=["subscription_plan", "updated_at"])
    current = tenant.subscriptions.filter(cancelled_at__isnull=True).first()
    now = timezone.now()
    if current:
        current.cancelled_at = now
        current.status = TenantSubscription.Status.CANCELLED
        current.save(update_fields=["cancelled_at", "status", "updated_at"])
    TenantSubscription.objects.create(
        tenant=tenant, plan=plan,
        status=TenantSubscription.Status.TRIAL if tenant.status == Tenant.Status.TRIAL else TenantSubscription.Status.ACTIVE,
        billing_cycle=billing_cycle, started_at=now, current_period_ends_at=tenant.trial_ends_at,
    )
    _audit(actor=actor, action=PlatformAuditLog.Action.PLAN_CHANGED, tenant=tenant, before_data=before_data, after_data=_tenant_snapshot(tenant), metadata={"plan_code": plan.code})
    return tenant
