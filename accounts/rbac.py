from dataclasses import dataclass
from functools import wraps

from core.exceptions import PermissionDeniedError
from accounts.models import TenantMembership


ROLE_ORDER = (
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.MANAGER,
    TenantMembership.Role.CASHIER,
)


@dataclass(frozen=True)
class RoleGuard:
    action: str
    allowed_roles: tuple[str, ...]


def user_has_tenant_role(user, tenant, allowed_roles):
    if not getattr(user, "is_authenticated", False) or tenant is None:
        return False
    if not tenant.is_active:
        return False
    if user.is_superuser:
        return True
    return TenantMembership.objects.filter(
        tenant=tenant,
        user=user,
        is_active=True,
        role__in=allowed_roles,
    ).exists()


def require_tenant_role(user, tenant, allowed_roles, action):
    if not user_has_tenant_role(user, tenant, allowed_roles):
        tenant_identifier = tenant.pk if tenant is not None else "unknown"
        raise PermissionDeniedError(f"{action} is not permitted for this user on tenant {tenant_identifier}.")


def tenant_role_required(*allowed_roles, action_name="this action"):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            tenant = getattr(request, "tenant", None)
            require_tenant_role(request.user, tenant, allowed_roles, action_name)
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator
