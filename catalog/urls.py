from django.urls import path

from catalog.views import (
    category_create,
    category_edit,
    category_list,
    category_toggle_active,
    product_create,
    product_detail,
    product_edit,
    product_list,
    product_toggle_active,
)


app_name = "catalog"

urlpatterns = [
    path("categories/", category_list, name="category-list"),
    path("categories/add/", category_create, name="category-create"),
    path("categories/<int:category_id>/edit/", category_edit, name="category-edit"),
    path("categories/<int:category_id>/toggle-active/", category_toggle_active, name="category-toggle-active"),
    path("products/", product_list, name="product-list"),
    path("products/add/", product_create, name="product-create"),
    path("products/<int:product_id>/", product_detail, name="product-detail"),
    path("products/<int:product_id>/edit/", product_edit, name="product-edit"),
    path("products/<int:product_id>/toggle-active/", product_toggle_active, name="product-toggle-active"),
]
