from accounts.models import TenantMembership
from core.tenant_context import reset_current_tenant_id, set_current_tenant_id


class CurrentTenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = None
        tenant = getattr(request, "tenant", None)
        if tenant is None and getattr(request, "user", None) is not None and request.user.is_authenticated:
            tenant = self._resolve_tenant_for_user(request)
            if tenant is not None:
                request.tenant = tenant
        if tenant is not None:
            token = set_current_tenant_id(tenant.pk)
        try:
            return self.get_response(request)
        finally:
            if token is not None:
                reset_current_tenant_id(token)

    def _resolve_tenant_for_user(self, request):
        selected_tenant_id = request.session.get("current_tenant_id")
        if selected_tenant_id:
            membership = (
                TenantMembership.objects.select_related("tenant")
                .filter(
                    user=request.user,
                    tenant_id=selected_tenant_id,
                    tenant__is_active=True,
                    is_active=True,
                )
                .first()
            )
            if membership is not None:
                return membership.tenant
            request.session.pop("current_tenant_id", None)

        memberships = (
            TenantMembership.objects.select_related("tenant")
            .filter(user=request.user, tenant__is_active=True, is_active=True)
            .order_by("tenant_id")
        )
        memberships = list(memberships[:2])
        if len(memberships) == 1:
            tenant = memberships[0].tenant
            request.session["current_tenant_id"] = tenant.pk
            return tenant
        return None
