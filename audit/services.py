from collections.abc import Mapping
import hashlib
import hmac
import json

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction

from audit.models import AuditEvent
from tenants.models import Tenant


def _coerce_mapping(value):
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    return value


def _target_details(target):
    content_type = ContentType.objects.get_for_model(target, for_concrete_model=False)
    return content_type, str(target.pk), str(target)


def _event_digest(*, tenant_id, actor_id, action, content_type_id, object_id, target_label,
                  before_data, after_data, metadata, previous_hash, hash_version=1, key=None):
    payload = {
        "version": hash_version,
        "tenant_id": tenant_id,
        "actor_id": actor_id,
        "action": action,
        "target_content_type_id": content_type_id,
        "target_object_id": object_id,
        "target_label": target_label,
        "before_data": before_data,
        "after_data": after_data,
        "metadata": metadata,
        "previous_hash": previous_hash,
    }
    canonical = json.dumps(
        payload, cls=DjangoJSONEncoder, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    secret = key if key is not None else settings.AUDIT_LOG_HMAC_KEY
    return hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


@transaction.atomic
def log_audit_event(*, tenant, actor, action, target, before_data=None, after_data=None, metadata=None):
    content_type, object_id, target_label = _target_details(target)
    before_data = _coerce_mapping(before_data)
    after_data = _coerce_mapping(after_data)
    metadata = _coerce_mapping(metadata)
    # One inexpensive row lock serializes each tenant's chain without blocking other tenants.
    Tenant._base_manager.select_for_update().only("pk").get(pk=tenant.pk)
    previous_hash = (
        AuditEvent._base_manager.filter(tenant_id=tenant.pk)
        .exclude(integrity_hash="")
        .order_by("-id")
        .values_list("integrity_hash", flat=True)
        .first()
        or ""
    )
    integrity_hash = _event_digest(
        tenant_id=tenant.pk,
        actor_id=actor.pk,
        action=action,
        content_type_id=content_type.pk,
        object_id=object_id,
        target_label=target_label,
        before_data=before_data,
        after_data=after_data,
        metadata=metadata,
        previous_hash=previous_hash,
    )
    return AuditEvent.objects.create(
        tenant=tenant,
        actor=actor,
        action=action,
        target_content_type=content_type,
        target_object_id=object_id,
        target_label=target_label,
        before_data=before_data,
        after_data=after_data,
        metadata=metadata,
        previous_hash=previous_hash,
        integrity_hash=integrity_hash,
    )


def verify_audit_chain(*, tenant):
    """Return verification details; blank-hash rows are a readable legacy prefix."""
    previous_hash = ""
    checked = 0
    legacy = 0
    for event in AuditEvent._base_manager.filter(tenant_id=tenant.pk).order_by("id").iterator(chunk_size=1000):
        if not event.integrity_hash:
            legacy += 1
            continue
        digests = [
            _event_digest(
                tenant_id=event.tenant_id,
                actor_id=event.actor_id,
                action=event.action,
                content_type_id=event.target_content_type_id,
                object_id=event.target_object_id,
                target_label=event.target_label,
                before_data=event.before_data,
                after_data=event.after_data,
                metadata=event.metadata,
                previous_hash=event.previous_hash,
                hash_version=event.hash_version,
                key=key,
            )
            for key in [settings.AUDIT_LOG_HMAC_KEY, *settings.AUDIT_LOG_HMAC_KEY_FALLBACKS]
        ]
        valid_digest = any(hmac.compare_digest(event.integrity_hash, digest) for digest in digests)
        if event.previous_hash != previous_hash or not valid_digest:
            return {"valid": False, "checked": checked, "legacy": legacy, "failed_event_id": event.pk}
        previous_hash = event.integrity_hash
        checked += 1
    return {"valid": True, "checked": checked, "legacy": legacy, "failed_event_id": None}


def snapshot_purchase(purchase):
    return {
        "purchase_number": purchase.purchase_number,
        "supplier_id": purchase.supplier_id,
        "supplier_name": purchase.supplier.name if getattr(purchase, "supplier", None) else None,
        "status": purchase.status,
        "order_date": purchase.order_date.isoformat() if purchase.order_date else None,
        "expected_date": purchase.expected_date.isoformat() if purchase.expected_date else None,
        "received_date": purchase.received_date.isoformat() if purchase.received_date else None,
        "cancelled_date": purchase.cancelled_date.isoformat() if purchase.cancelled_date else None,
        "cancelled_reason": purchase.cancelled_reason,
        "notes": purchase.notes,
        "items": [
            {
                "product_id": item.product_id,
                "product_name": item.product.name if getattr(item, "product", None) else None,
                "quantity": item.quantity,
                "unit_cost": str(item.unit_cost),
                "line_total": str(item.line_total),
            }
            for item in purchase.items.select_related("product").all().order_by("id")
        ],
    }


def snapshot_stock_adjustment(adjustment):
    return {
        "adjustment_number": adjustment.adjustment_number,
        "reason": adjustment.reason,
        "status": adjustment.status,
        "notes": adjustment.notes,
        "posted_at": adjustment.posted_at.isoformat() if adjustment.posted_at else None,
        "cancelled_at": adjustment.cancelled_at.isoformat() if adjustment.cancelled_at else None,
        "cancel_reason": adjustment.cancel_reason,
        "items": [
            {
                "product_id": item.product_id,
                "product_name": item.product.name if getattr(item, "product", None) else None,
                "quantity_before": item.quantity_before,
                "quantity_after": item.quantity_after,
                "quantity_delta": item.quantity_delta,
                "note": item.note,
            }
            for item in adjustment.items.select_related("product").all().order_by("id")
        ],
    }
