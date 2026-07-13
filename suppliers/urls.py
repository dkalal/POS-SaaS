from django.urls import path

from suppliers.views import supplier_edit, supplier_list, supplier_toggle_active


app_name = "suppliers"

urlpatterns = [
    path("", supplier_list, name="supplier-list"),
    path("<int:supplier_id>/edit/", supplier_edit, name="supplier-edit"),
    path("<int:supplier_id>/toggle-active/", supplier_toggle_active, name="supplier-toggle-active"),
]
