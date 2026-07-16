from functools import wraps

from django.core.exceptions import PermissionDenied

from accounts.models import TenantMembership


def is_platform_admin(user):
    """Platform operators are Django superusers with no workspace membership."""
    return bool(
        getattr(user, "is_authenticated", False)
        and user.is_superuser
        and not TenantMembership.objects.filter(user=user).exists()
    )


def platform_admin_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if getattr(request, "tenant", None) is not None or not is_platform_admin(request.user):
            raise PermissionDenied("Platform administration is restricted to platform administrators.")
        return view_func(request, *args, **kwargs)

    return wrapped
