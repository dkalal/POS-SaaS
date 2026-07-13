from django.db import transaction
from django.utils.text import slugify

from accounts.models import TenantMembership
from api.models import APIKey
from tenants.models import Tenant


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

