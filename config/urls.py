from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("dashboard.urls")),
    path("catalog/", include("catalog.urls")),
    path("suppliers/", include("suppliers.urls")),
    path("purchasing/", include("purchasing.urls")),
    path("sales/", include("sales.urls")),
    path("inventory/", include("inventory.urls")),
    path("audit/", include("audit.urls")),
    path("accounts/", include("accounts.urls")),
    path("", include("tenants.urls")),
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("api/v1/", include("api.urls")),
]
