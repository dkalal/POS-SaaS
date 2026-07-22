from django.urls import path

from reports import views


app_name = "reports"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("sales/", views.sales_report, name="sales"),
    path("sales/export.csv", views.sales_export, name="sales-export"),
    path("purchases/", views.purchase_report, name="purchases"),
    path("purchases/export.csv", views.purchase_export, name="purchases-export"),
    path("inventory/", views.inventory_report, name="inventory"),
    path("inventory/export.csv", views.inventory_export, name="inventory-export"),
    path("low-stock/", views.low_stock_report, name="low-stock"),
    path("low-stock/export.csv", views.low_stock_export, name="low-stock-export"),
    path("products/", views.product_performance_report, name="products"),
    path("products/export.csv", views.product_performance_export, name="products-export"),
    path("profit/", views.profit_report, name="profit"),
    path("profit/export.csv", views.profit_export, name="profit-export"),
]

