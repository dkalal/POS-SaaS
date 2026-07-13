from django.db import models


class TenantAwareManager(models.Manager):
    def get_queryset(self):
        queryset = super().get_queryset()
        if hasattr(queryset, "for_current_tenant"):
            return queryset.for_current_tenant()
        return queryset

