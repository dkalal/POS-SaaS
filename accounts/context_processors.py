from accounts.models import TenantMembership
from accounts.rbac import OWNER_ROLES


def session_identity(request):
    user = getattr(request, "user", None)
    tenant = getattr(request, "tenant", None)
    membership = None

    if user is not None and user.is_authenticated and tenant is not None:
        membership = (
            TenantMembership.objects.filter(
                tenant=tenant,
                user=user,
                tenant__is_active=True,
                status=TenantMembership.Status.ACTIVE,
                is_active=True,
            )
            .only("role")
            .first()
        )

    owner_roles = OWNER_ROLES
    is_owner_admin = bool(
        getattr(user, "is_authenticated", False)
        and membership is not None and membership.role in owner_roles
    )
    is_manager_or_above = bool(
        getattr(user, "is_authenticated", False)
        and membership is not None
        and membership.role in owner_roles + (TenantMembership.Role.MANAGER,)
    )
    can_open_register = bool(
        getattr(user, "is_authenticated", False)
        and membership is not None
        and membership.role in (
            *owner_roles,
            TenantMembership.Role.MANAGER,
            TenantMembership.Role.CASHIER,
        )
    )

    return {
        "current_membership": membership,
        "can_open_register": can_open_register,
        "can_view_sales_documents": can_open_register,
        "can_manage_quotations": is_manager_or_above,
        "can_manage_members": is_owner_admin,
        "can_manage_workspace_settings": is_owner_admin,
        "can_manage_api_keys": is_owner_admin,
        "can_manage_catalog": is_manager_or_above,
        "can_manage_purchases": is_manager_or_above,
        "can_manage_inventory": is_manager_or_above,
        "can_view_reports": is_manager_or_above,
        "available_workspaces": (
            TenantMembership.objects.select_related("tenant")
            .filter(user=user, status=TenantMembership.Status.ACTIVE, is_active=True,
                    tenant__is_active=True, tenant__status__in=("trial", "active"))
            .order_by("tenant__name")
            if getattr(user, "is_authenticated", False) else TenantMembership.objects.none()
        ),
    }
