from collections.abc import Mapping

from django.contrib.contenttypes.models import ContentType

from audit.models import AuditEvent


def _coerce_mapping(value):
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    return value


def _target_details(target):
    content_type = ContentType.objects.get_for_model(target, for_concrete_model=False)
    return content_type, str(target.pk), str(target)


def log_audit_event(*, tenant, actor, action, target, before_data=None, after_data=None, metadata=None):
    content_type, object_id, target_label = _target_details(target)
    return AuditEvent.objects.create(
        tenant=tenant,
        actor=actor,
        action=action,
        target_content_type=content_type,
        target_object_id=object_id,
        target_label=target_label,
        before_data=_coerce_mapping(before_data),
        after_data=_coerce_mapping(after_data),
        metadata=_coerce_mapping(metadata),
    )


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
