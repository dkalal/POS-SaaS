from django.urls import path

from catalog.views import category_edit, category_list, category_toggle_active, product_edit, product_list, product_toggle_active


app_name = "catalog"

urlpatterns = [
    path("categories/", category_list, name="category-list"),
    path("categories/<int:category_id>/edit/", category_edit, name="category-edit"),
    path("categories/<int:category_id>/toggle-active/", category_toggle_active, name="category-toggle-active"),
    path("products/", product_list, name="product-list"),
    path("products/<int:product_id>/edit/", product_edit, name="product-edit"),
    path("products/<int:product_id>/toggle-active/", product_toggle_active, name="product-toggle-active"),
]
