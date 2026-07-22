from datetime import timedelta
import hashlib
import hmac
import json

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection
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


def _platform_digest(*, actor_id, action, target_tenant_id, before_data, after_data,
                     metadata, previous_hash, hash_version=1, key=None):
    canonical = json.dumps(
        {
            "version": hash_version,
            "actor_id": actor_id,
            "action": action,
            "target_tenant_id": target_tenant_id,
            "before_data": before_data,
            "after_data": after_data,
            "metadata": metadata,
            "previous_hash": previous_hash,
        },
        cls=DjangoJSONEncoder,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    secret = key if key is not None else settings.AUDIT_LOG_HMAC_KEY
    return hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


@transaction.atomic
def _audit(*, actor, action, tenant=None, before_data=None, after_data=None, metadata=None):
    # A transaction-scoped advisory lock serializes this low-volume global chain.
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [0x504F534155444954])
    before_data = before_data or {}
    after_data = after_data or {}
    metadata = metadata or {}
    previous_hash = (
        PlatformAuditLog._base_manager.exclude(integrity_hash="")
        .order_by("-id")
        .values_list("integrity_hash", flat=True)
        .first()
        or ""
    )
    integrity_hash = _platform_digest(
        actor_id=actor.pk,
        action=action,
        target_tenant_id=tenant.pk if tenant else None,
        before_data=before_data,
        after_data=after_data,
        metadata=metadata,
        previous_hash=previous_hash,
    )
    return PlatformAuditLog.objects.create(
        actor=actor,
        target_tenant=tenant,
        action=action,
        before_data=before_data,
        after_data=after_data,
        metadata=metadata,
        previous_hash=previous_hash,
        integrity_hash=integrity_hash,
    )


def verify_platform_audit_chain():
    previous_hash = ""
    checked = legacy = 0
    keys = [settings.AUDIT_LOG_HMAC_KEY, *settings.AUDIT_LOG_HMAC_KEY_FALLBACKS]
    for event in PlatformAuditLog._base_manager.order_by("id").iterator(chunk_size=1000):
        if not event.integrity_hash:
            legacy += 1
            continue
        digests = [
            _platform_digest(
                actor_id=event.actor_id,
                action=event.action,
                target_tenant_id=event.target_tenant_id,
                before_data=event.before_data,
                after_data=event.after_data,
                metadata=event.metadata,
                previous_hash=event.previous_hash,
                hash_version=event.hash_version,
                key=key,
            )
            for key in keys
        ]
        if event.previous_hash != previous_hash or not any(
            hmac.compare_digest(event.integrity_hash, digest) for digest in digests
        ):
            return {"valid": False, "checked": checked, "legacy": legacy, "failed_event_id": event.pk}
        previous_hash = event.integrity_hash
        checked += 1
    return {"valid": True, "checked": checked, "legacy": legacy, "failed_event_id": None}


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
def change_tenant_status(*, tenant, status, actor, expected_status=None):
    if status not in {Tenant.Status.ACTIVE, Tenant.Status.SUSPENDED, Tenant.Status.CANCELLED}:
        raise ValueError("Unsupported tenant status transition.")
    tenant = Tenant.objects.select_for_update().get(pk=tenant.pk)
    if expected_status and tenant.status != expected_status:
        raise ValueError("The workspace status changed before confirmation. Review it and try again.")
    if tenant.status == status:
        raise ValueError("The workspace already has that status.")
    allowed_transitions = {
        Tenant.Status.TRIAL: {Tenant.Status.ACTIVE, Tenant.Status.SUSPENDED, Tenant.Status.CANCELLED},
        Tenant.Status.ACTIVE: {Tenant.Status.SUSPENDED, Tenant.Status.CANCELLED},
        Tenant.Status.SUSPENDED: {Tenant.Status.ACTIVE, Tenant.Status.CANCELLED},
        Tenant.Status.CANCELLED: {Tenant.Status.ACTIVE},
    }
    if status not in allowed_transitions.get(tenant.status, set()):
        raise ValueError("That workspace lifecycle transition is not allowed.")
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
