from django.urls import path

from dashboard.views import dashboard, select_tenant


urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("tenants/select/<int:tenant_id>/", select_tenant, name="select_tenant"),
]
