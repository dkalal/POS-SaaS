from dataclasses import dataclass
from functools import wraps

from core.exceptions import PermissionDeniedError
from accounts.models import TenantMembership


OWNER_ROLES = (
    TenantMembership.Role.OWNER,
    TenantMembership.Role.OWNER_ADMIN,
    TenantMembership.Role.ADMIN,
)

ROLE_LEVEL = {
    TenantMembership.Role.VIEWER: 0,
    TenantMembership.Role.CASHIER: 1,
    TenantMembership.Role.MANAGER: 2,
    TenantMembership.Role.ADMIN: 3,
    TenantMembership.Role.OWNER_ADMIN: 4,
    TenantMembership.Role.OWNER: 4,
}


def active_membership_for(user, tenant):
    if not getattr(user, "is_authenticated", False) or tenant is None:
        return None
    return TenantMembership.objects.filter(
        tenant=tenant,
        user=user,
        status=TenantMembership.Status.ACTIVE,
        is_active=True,
    ).first()


def role_level(role):
    return ROLE_LEVEL.get(role, -1)


def grantable_roles_for(membership):
    """Roles strictly below the actor's authority, excluding legacy invite-only gaps."""
    if membership is None:
        return ()
    actor_level = role_level(membership.role)
    return tuple(role for role, _label in TenantMembership.Role.choices if role_level(role) < actor_level)


@dataclass(frozen=True)
class RoleGuard:
    action: str
    allowed_roles: tuple[str, ...]


def user_has_tenant_role(user, tenant, allowed_roles):
    if not getattr(user, "is_authenticated", False) or tenant is None:
        return False
    if not tenant.is_active or tenant.status not in (tenant.Status.TRIAL, tenant.Status.ACTIVE):
        return False
    effective_roles = tuple(allowed_roles)
    if TenantMembership.Role.OWNER_ADMIN in effective_roles:
        effective_roles = tuple(dict.fromkeys((*effective_roles, *OWNER_ROLES)))
    return TenantMembership.objects.filter(
        tenant=tenant,
        user=user,
        status=TenantMembership.Status.ACTIVE,
        is_active=True,
        role__in=effective_roles,
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
