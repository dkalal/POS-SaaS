from django.contrib import admin
from django.urls import include, path

from config import health

handler400 = "config.views.bad_request"
handler403 = "config.views.permission_denied"
handler404 = "config.views.page_not_found"
handler500 = "config.views.server_error"

urlpatterns = [
    path("healthz/", health.liveness, name="liveness"),
    path("readyz/", health.readiness, name="readiness"),
    path("platform/", include("platform_admin.urls")),
    path("", include("dashboard.urls")),
    path("catalog/", include("catalog.urls")),
    path("suppliers/", include("suppliers.urls")),
    path("purchasing/", include("purchasing.urls")),
    path("sales/", include("sales.urls")),
    path("inventory/", include("inventory.urls")),
    path("reports/", include("reports.urls")),
    path("audit/", include("audit.urls")),
    path("accounts/", include("accounts.urls")),
    path("", include("tenants.urls")),
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("api/v1/", include("api.urls")),
]
