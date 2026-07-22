from django.db import models

from core.managers import TenantAwareManager
from core.tenant_context import get_current_tenant_id

class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TenantScopedQuerySet(models.QuerySet):
    def for_current_tenant(self):
        tenant_id = get_current_tenant_id()
        if tenant_id is None:
            return self
        return self.filter(tenant_id=tenant_id)


class AppendOnlyTenantQuerySet(TenantScopedQuerySet):
    """Reject application-level mutation of ledger/audit rows after insertion."""

    def update(self, **kwargs):
        raise ValueError(f"{self.model.__name__} is append-only and cannot be updated.")

    def delete(self):
        raise ValueError(f"{self.model.__name__} is append-only and cannot be deleted.")

    def bulk_create(self, objs, **kwargs):
        raise ValueError(f"{self.model.__name__} must be inserted through its integrity service.")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValueError(f"{self.model.__name__} is append-only and cannot be updated.")


class TenantScopedModel(TimeStampedModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT)

    objects = TenantAwareManager.from_queryset(TenantScopedQuerySet)()

    class Meta:
        abstract = True


class ActiveTenantScopedModel(TenantScopedModel):
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True
