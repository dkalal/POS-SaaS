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


class TenantScopedModel(TimeStampedModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT)

    objects = TenantAwareManager.from_queryset(TenantScopedQuerySet)()

    class Meta:
        abstract = True


class ActiveTenantScopedModel(TenantScopedModel):
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True
