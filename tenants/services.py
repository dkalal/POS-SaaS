from django.db import transaction
from django.utils.text import slugify

from accounts.models import TenantMembership
from api.models import APIKey
from tenants.models import Tenant
from accounts.rbac import OWNER_ROLES, active_membership_for
from audit.models import AuditEvent
from audit.services import log_audit_event


def _build_unique_slug(base_value):
    base_slug = slugify(base_value) or "tenant"
    candidate = base_slug
    suffix = 2
    while Tenant.objects.filter(slug=candidate).exists():
        candidate = f"{base_slug}-{suffix}"
        suffix += 1
    return candidate


@transaction.atomic
def bootstrap_first_tenant(*, owner, tenant_name, tenant_slug="", api_key_label, api_key_can_view_cost=False):
    slug_source = tenant_slug or tenant_name
    tenant = Tenant.objects.create(
        name=tenant_name.strip(),
        slug=_build_unique_slug(slug_source),
    )
    membership = TenantMembership.objects.create(
        tenant=tenant,
        user=owner,
        role=TenantMembership.Role.OWNER_ADMIN,
        is_active=True,
    )
    api_key, raw_key = APIKey.create_key(
        tenant=tenant,
        label=api_key_label.strip(),
        created_by=owner,
        can_view_cost=api_key_can_view_cost,
    )
    return tenant, membership, api_key, raw_key


@transaction.atomic
def update_workspace_settings(*, tenant, actor, section, values):
    membership = active_membership_for(actor, tenant)
    if membership is None or membership.role not in OWNER_ROLES:
        raise ValueError("You do not have permission to change workspace settings.")
    section_fields = {
        "business": (
            "name", "contact_email", "contact_phone", "address",
            "tax_identification_number", "vat_registration_number",
        ),
        "regional": ("currency", "timezone"),
        "receipt": ("receipt_business_details", "receipt_footer", "receipt_prefix"),
        "operational": ("default_track_inventory", "default_reorder_level"),
    }
    fields = section_fields.get(section)
    if fields is None:
        raise ValueError("Unsupported settings section.")
    locked = Tenant.objects.select_for_update().get(pk=tenant.pk)
    before = {field: getattr(locked, field) for field in fields}
    for field in fields:
        setattr(locked, field, values[field])
    locked.save(update_fields=[*fields, "updated_at"])
    after = {field: getattr(locked, field) for field in fields}
    log_audit_event(
        tenant=locked,
        actor=actor,
        action=AuditEvent.Action.WORKSPACE_SETTINGS_UPDATED,
        target=locked,
        before_data=before,
        after_data=after,
        metadata={"section": section},
    )
    return locked
