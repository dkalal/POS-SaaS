from django.urls import path

from purchasing.views import purchase_create, purchase_detail, purchase_duplicate, purchase_edit, purchase_list


app_name = "purchasing"

urlpatterns = [
    path("", purchase_list, name="purchase-list"),
    path("create/", purchase_create, name="purchase-create"),
    path("<int:purchase_id>/edit/", purchase_edit, name="purchase-edit"),
    path("<int:purchase_id>/duplicate/", purchase_duplicate, name="purchase-duplicate"),
    path("<int:purchase_id>/", purchase_detail, name="purchase-detail"),
]
