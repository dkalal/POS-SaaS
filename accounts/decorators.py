from accounts.rbac import require_tenant_role


class TenantRoleRequiredMixin:
    allowed_roles = ()
    action_name = "this action"

    def dispatch(self, request, *args, **kwargs):
        require_tenant_role(request.user, getattr(request, "tenant", None), self.allowed_roles, self.action_name)
        return super().dispatch(request, *args, **kwargs)

