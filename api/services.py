from django.db import transaction

from api.models import APIKey


@transaction.atomic
def create_api_key(*, tenant, label, created_by=None, can_view_cost=False, notes=""):
    return APIKey.create_key(
        tenant=tenant,
        label=label,
        created_by=created_by,
        can_view_cost=can_view_cost,
        notes=notes,
    )


@transaction.atomic
def revoke_api_key(*, api_key):
    api_key.revoke()
    return api_key

