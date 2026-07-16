from django.urls import path

from platform_admin import views

app_name = "platform_admin"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("tenants/", views.tenant_list, name="tenant-list"),
    path("tenants/new/", views.tenant_create, name="tenant-create"),
    path("tenants/<int:tenant_id>/", views.tenant_detail, name="tenant-detail"),
    path("tenants/<int:tenant_id>/status/", views.tenant_status, name="tenant-status"),
    path("tenants/<int:tenant_id>/trial/", views.tenant_trial_extend, name="tenant-trial-extend"),
    path("tenants/<int:tenant_id>/plan/", views.tenant_plan_change, name="tenant-plan-change"),
    path("plans/", views.plan_list, name="plan-list"),
    path("plans/new/", views.plan_create, name="plan-create"),
    path("plans/<int:plan_id>/", views.plan_edit, name="plan-edit"),
]
