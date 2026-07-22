from django.urls import path

from inventory.views import adjustment_create, adjustment_detail, adjustment_edit, adjustment_list, inventory_overview, movement_ledger, product_stock_history


app_name = "inventory"

urlpatterns = [
    path("", inventory_overview, name="overview"),
    path("movements/", movement_ledger, name="movement-ledger"),
    path("products/<int:product_id>/history/", product_stock_history, name="product-history"),
    path("adjustments/", adjustment_list, name="adjustment-list"),
    path("adjustments/create/", adjustment_create, name="adjustment-create"),
    path("adjustments/<int:adjustment_id>/edit/", adjustment_edit, name="adjustment-edit"),
    path("adjustments/<int:adjustment_id>/", adjustment_detail, name="adjustment-detail"),
]
