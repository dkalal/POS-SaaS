from django.urls import path

from audit import views


app_name = "audit"

urlpatterns = [
    path("", views.audit_list, name="audit-list"),
    path("<int:event_id>/", views.audit_detail, name="audit-detail"),
]
