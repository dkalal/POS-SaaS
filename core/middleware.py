import logging
import time
import uuid

from accounts.models import TenantMembership
from core.tenant_context import reset_current_tenant_id, set_current_tenant_id
from django.utils.cache import patch_cache_control
from django.shortcuts import redirect


request_logger = logging.getLogger("pos_saas.request")


class RequestLoggingMiddleware:
    """Emit safe request telemetry without URLs, credentials, or request bodies."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.headers.get("X-Request-ID", "")
        if not request_id.isascii() or not request_id.replace("-", "").isalnum() or len(request_id) > 64:
            request_id = str(uuid.uuid4())
        started = time.perf_counter()
        try:
            response = self.get_response(request)
        except Exception:
            request_logger.exception(
                "request_failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise

        response["X-Request-ID"] = request_id
        tenant = getattr(request, "tenant", None)
        request_logger.info(
            "request_complete",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "tenant_id": getattr(tenant, "pk", None),
            },
        )
        return response


class AuthenticatedResponseCacheControlMiddleware:
    """Prevent authenticated or credential-entry pages from being reused by browser history."""

    credential_paths = {"/accounts/login/", "/accounts/logout/"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if (user is not None and user.is_authenticated) or request.path in self.credential_paths:
            patch_cache_control(
                response,
                private=True,
                no_cache=True,
                no_store=True,
                must_revalidate=True,
                max_age=0,
            )
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
        return response


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
            verification = getattr(getattr(request, "user", None), "email_verification", None)
            public_account_path = request.path.startswith("/accounts/") and any(
                marker in request.path for marker in ("/signup", "/verify", "/login", "/logout", "/invitations/")
            )
            if (
                getattr(request.user, "is_authenticated", False)
                and tenant is not None
                and verification is not None
                and not verification.is_verified
                and not public_account_path
            ):
                return redirect("verify_required")
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
                    tenant__status__in=("trial", "active"),
                    status=TenantMembership.Status.ACTIVE,
                    is_active=True,
                )
                .first()
            )
            if membership is not None:
                return membership.tenant
            request.session.pop("current_tenant_id", None)

        memberships = (
            TenantMembership.objects.select_related("tenant")
            .filter(user=request.user, tenant__is_active=True, tenant__status__in=("trial", "active"), status=TenantMembership.Status.ACTIVE, is_active=True)
            .order_by("tenant_id")
        )
        memberships = list(memberships[:2])
        if len(memberships) == 1:
            tenant = memberships[0].tenant
            request.session["current_tenant_id"] = tenant.pk
            return tenant
        return None
