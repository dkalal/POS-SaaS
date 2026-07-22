app_name = "sales"

from django.urls import path

from sales.views import (
    invoice_detail,
    invoice_list,
    quotation_detail,
    quotation_create,
    quotation_edit,
    quotation_list,
    quotation_status,
    receipt_detail,
    receipt_list,
    receipt_print,
    register,
    sale_detail,
    sale_list,
)


urlpatterns = [
    path("", sale_list, name="sale-list"),
    path("register/", register, name="register"),
    path("quotations/", quotation_list, name="quotation-list"),
    path("quotations/create/", quotation_create, name="quotation-create"),
    path("quotations/<int:quotation_id>/edit/", quotation_edit, name="quotation-edit"),
    path("quotations/<int:quotation_id>/status/", quotation_status, name="quotation-status"),
    path("quotations/<int:quotation_id>/", quotation_detail, name="quotation-detail"),
    path("invoices/", invoice_list, name="invoice-list"),
    path("invoices/<int:invoice_id>/", invoice_detail, name="invoice-detail"),
    path("receipts/", receipt_list, name="receipt-list"),
    path("receipts/<int:receipt_id>/", receipt_detail, name="receipt-detail"),
    path("receipts/<int:receipt_id>/print/", receipt_print, name="receipt-print"),
    path("<int:sale_id>/", sale_detail, name="sale-detail"),
]
