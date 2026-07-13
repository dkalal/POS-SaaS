from accounts.models import TenantMembership


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
                is_active=True,
            )
            .only("role")
            .first()
        )

    is_owner_admin = bool(
        getattr(user, "is_authenticated", False)
        and (
            getattr(user, "is_superuser", False)
            or (membership is not None and membership.role == TenantMembership.Role.OWNER_ADMIN)
        )
    )
    is_manager_or_above = bool(
        getattr(user, "is_authenticated", False)
        and (
            getattr(user, "is_superuser", False)
            or (
                membership is not None
                and membership.role
                in (TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.MANAGER)
            )
        )
    )
    can_open_register = bool(
        getattr(user, "is_authenticated", False)
        and (
            getattr(user, "is_superuser", False)
            or (
                membership is not None
                and membership.role
                in (
                    TenantMembership.Role.OWNER_ADMIN,
                    TenantMembership.Role.MANAGER,
                    TenantMembership.Role.CASHIER,
                )
            )
        )
    )

    return {
        "current_membership": membership,
        "can_open_register": can_open_register,
        "can_manage_members": is_owner_admin,
        "can_manage_api_keys": is_owner_admin,
        "can_manage_catalog": is_manager_or_above,
        "can_manage_purchases": is_manager_or_above,
        "can_manage_inventory": is_manager_or_above,
        "can_view_reports": is_owner_admin,
    }
