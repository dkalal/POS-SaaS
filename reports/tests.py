import csv
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from accounts.models import TenantMembership
from catalog.models import Category, Product
from inventory.models import Stock
from payments.models import Payment
from purchasing.models import Purchase, PurchaseItem
from sales.models import Invoice, Quotation, Sale, SaleItem
from suppliers.models import Supplier
from tenants.models import Tenant


class ReportViewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.manager = user_model.objects.create_user("manager", password="test-pass-123")
        self.cashier = user_model.objects.create_user("cashier", password="test-pass-123")
        self.other_user = user_model.objects.create_user("other", password="test-pass-123")
        self.tenant = Tenant.objects.create(name="Report Shop", slug="report-shop")
        self.other_tenant = Tenant.objects.create(name="Other Shop", slug="other-shop")
        TenantMembership.objects.create(tenant=self.tenant, user=self.manager, role=TenantMembership.Role.MANAGER)
        TenantMembership.objects.create(tenant=self.tenant, user=self.cashier, role=TenantMembership.Role.CASHIER)
        TenantMembership.objects.create(tenant=self.other_tenant, user=self.other_user, role=TenantMembership.Role.MANAGER)
        self.category = Category.objects.create(tenant=self.tenant, name="Goods", slug="goods")
        self.product = Product.objects.create(
            tenant=self.tenant, category=self.category, name="Tenant product", sku="TEN-1",
            cost_price=Decimal("4.00"), sale_price=Decimal("10.00"), reorder_level=3,
        )
        self.service = Product.objects.create(
            tenant=self.tenant, category=self.category, name="Tenant service", sku="SVC-1", track_inventory=False,
        )
        Stock.objects.create(tenant=self.tenant, product=self.product, quantity=2)
        other_category = Category.objects.create(tenant=self.other_tenant, name="Other", slug="other")
        self.other_product = Product.objects.create(tenant=self.other_tenant, category=other_category, name="Secret product", sku="SEC-1")
        Stock.objects.create(tenant=self.other_tenant, product=self.other_product, quantity=99)
        self.supplier = Supplier.objects.create(tenant=self.tenant, name="Tenant supplier")
        self.other_supplier = Supplier.objects.create(tenant=self.other_tenant, name="Secret supplier")
        self.login_manager()

    def login_manager(self):
        self.client.force_login(self.manager)
        session = self.client.session
        session["current_tenant_id"] = self.tenant.pk
        session.save()

    def create_sale(self, *, number, status=Sale.Status.COMPLETED, payment_status=Payment.Status.COMPLETED, cost=Decimal("4.00"), product=None, created_at=None):
        product = product or self.product
        sale = Sale.objects.create(
            tenant=product.tenant, sale_number=number, status=status, subtotal=Decimal("20.00"),
            grand_total=Decimal("20.00"), cashier=self.manager if product.tenant == self.tenant else self.other_user,
        )
        SaleItem.objects.create(
            tenant=product.tenant, sale=sale, product=product, quantity=2, unit_price=Decimal("10.00"),
            unit_cost_snapshot=cost, line_subtotal=Decimal("20.00"), line_total=Decimal("20.00"),
        )
        Payment.objects.create(
            tenant=product.tenant, sale=sale, method=Payment.Method.CASH, amount=Decimal("20.00"),
            status=payment_status, received_by=sale.cashier,
        )
        Invoice.objects.create(
            tenant=product.tenant, invoice_number=f"I-{number}", sale=sale, status=Invoice.Status.PAID,
            subtotal=sale.subtotal, grand_total=sale.grand_total, created_by=sale.cashier,
        )
        if created_at:
            Sale.objects.filter(pk=sale.pk).update(created_at=created_at)
            sale.refresh_from_db()
        return sale

    def create_purchase(self, *, number, status, tenant=None):
        tenant = tenant or self.tenant
        purchase = Purchase.objects.create(
            tenant=tenant, purchase_number=number, supplier=self.supplier if tenant == self.tenant else self.other_supplier,
            status=status, order_date=timezone.localdate(), received_date=timezone.now() if status == Purchase.Status.RECEIVED else None,
            created_by=self.manager if tenant == self.tenant else self.other_user,
            received_by=(self.manager if tenant == self.tenant else self.other_user) if status == Purchase.Status.RECEIVED else None,
        )
        PurchaseItem.objects.create(
            tenant=tenant, purchase=purchase, product=self.product if tenant == self.tenant else self.other_product,
            quantity=5, unit_cost=Decimal("3.00"), line_total=Decimal("15.00"),
        )
        return purchase

    def test_all_report_pages_are_tenant_scoped(self):
        self.create_sale(number="VISIBLE")
        self.create_sale(number="SECRET", product=self.other_product)
        self.create_purchase(number="P-VISIBLE", status=Purchase.Status.RECEIVED)
        self.create_purchase(number="P-SECRET", status=Purchase.Status.RECEIVED, tenant=self.other_tenant)
        for name in ("landing", "sales", "purchases", "inventory", "low-stock", "products", "profit"):
            response = self.client.get(reverse(f"reports:{name}"))
            self.assertEqual(response.status_code, 200, name)
            self.assertNotContains(response, "Secret product")
            self.assertNotContains(response, "P-SECRET")

    def test_every_export_is_tenant_scoped(self):
        self.create_sale(number="VISIBLE")
        self.create_sale(number="SECRET", product=self.other_product)
        self.create_purchase(number="P-VISIBLE", status=Purchase.Status.RECEIVED)
        self.create_purchase(number="P-SECRET", status=Purchase.Status.RECEIVED, tenant=self.other_tenant)
        for name in ("sales-export", "purchases-export", "inventory-export", "low-stock-export", "products-export", "profit-export"):
            response = self.client.get(reverse(f"reports:{name}"))
            self.assertEqual(response.status_code, 200, name)
            content = response.content.decode()
            self.assertNotIn("SECRET", content)
            self.assertNotIn("Secret product", content)
            self.assertNotIn("Secret supplier", content)

    def test_cashier_cannot_view_or_export_reports(self):
        self.client.force_login(self.cashier)
        session = self.client.session
        session["current_tenant_id"] = self.tenant.pk
        session.save()
        for name in ("sales", "sales-export", "purchases", "inventory", "profit-export"):
            self.assertEqual(self.client.get(reverse(f"reports:{name}")).status_code, 403, name)

    def test_sales_totals_include_only_completed_fully_paid_sales(self):
        self.create_sale(number="GOOD")
        self.create_sale(number="CANCELLED", status=Sale.Status.CANCELLED)
        self.create_sale(number="FAILED", payment_status=Payment.Status.FAILED)
        Quotation.objects.create(
            tenant=self.tenant, quotation_number="QUOTE", status=Quotation.Status.ACCEPTED,
            subtotal=Decimal("999.00"), grand_total=Decimal("999.00"), created_by=self.manager,
        )
        response = self.client.get(reverse("reports:sales"))
        self.assertContains(response, "TZS 20.00")
        self.assertContains(response, "GOOD")
        self.assertNotContains(response, "CANCELLED")
        self.assertNotContains(response, "FAILED")
        self.assertNotContains(response, "QUOTE")

    def test_purchase_report_includes_received_only(self):
        self.create_purchase(number="RECEIVED", status=Purchase.Status.RECEIVED)
        self.create_purchase(number="DRAFT", status=Purchase.Status.DRAFT)
        self.create_purchase(number="VOID", status=Purchase.Status.CANCELLED)
        response = self.client.get(reverse("reports:purchases"))
        self.assertContains(response, "RECEIVED")
        self.assertContains(response, "TZS 15.00")
        self.assertNotContains(response, "DRAFT")
        self.assertNotContains(response, "VOID")

    def test_inventory_excludes_services_and_calculates_stock_status(self):
        response = self.client.get(reverse("reports:inventory"))
        self.assertContains(response, "Tenant product")
        self.assertNotContains(response, "Tenant service")
        self.assertContains(response, "Low stock")
        self.assertContains(response, "Stock valuation")
        self.assertContains(response, "Unavailable")

    def test_profit_uses_snapshot_and_is_unavailable_for_ambiguous_zero_cost(self):
        self.create_sale(number="PROFIT", cost=Decimal("4.00"))
        response = self.client.get(reverse("reports:profit"))
        self.assertContains(response, "TZS 8.00")
        self.assertContains(response, "TZS 12.00")
        self.assertContains(response, "60.00%")
        self.create_sale(number="UNKNOWN-COST", cost=Decimal("0.00"))
        response = self.client.get(reverse("reports:profit"))
        self.assertContains(response, "Gross profit estimate unavailable")
        self.assertEqual(self.client.get(reverse("reports:profit-export")).status_code, 409)

    def test_date_filter_and_csv_match_active_scope(self):
        self.create_sale(number="TODAY")
        self.create_sale(number="OLD", created_at=timezone.now() - timedelta(days=40))
        query = {"period": "custom", "date_from": timezone.localdate().isoformat(), "date_to": timezone.localdate().isoformat()}
        page = self.client.get(reverse("reports:sales"), query)
        export = self.client.get(reverse("reports:sales-export"), query)
        self.assertContains(page, "TODAY")
        self.assertNotContains(page, "OLD")
        rows = list(csv.reader(StringIO(export.content.decode())))
        self.assertEqual(export.status_code, 200)
        self.assertEqual(len(rows), 2)
        self.assertIn("TODAY", rows[1])
        self.assertNotIn("OLD", export.content.decode())

    def test_cross_tenant_filter_id_is_rejected(self):
        response = self.client.get(reverse("reports:products"), {"period": "month", "product": self.other_product.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid choice")
        export = self.client.get(reverse("reports:products-export"), {"period": "month", "product": self.other_product.pk})
        self.assertEqual(export.status_code, 400)

    def test_invalid_and_overlong_date_ranges_are_rejected(self):
        reversed_range = self.client.get(reverse("reports:sales"), {"period": "custom", "date_from": "2026-07-20", "date_to": "2026-07-01"})
        self.assertContains(reversed_range, "start date must be on or before", status_code=200)
        export = self.client.get(reverse("reports:sales-export"), {"period": "custom", "date_from": "2020-01-01", "date_to": "2026-01-01"})
        self.assertEqual(export.status_code, 400)

    def test_sales_details_are_paginated_and_pages_are_accessible(self):
        for index in range(31):
            self.create_sale(number=f"PAGE-{index:02d}")
        response = self.client.get(reverse("reports:sales"))
        self.assertContains(response, "Page 1 of 2")
        self.assertContains(response, 'aria-label="Report filters"')
        self.assertContains(response, 'class="responsive-table-wrap"')

    def test_sales_report_query_count_does_not_grow_with_detail_rows(self):
        self.create_sale(number="QUERY-BASE")
        with CaptureQueriesContext(connection) as small_context:
            self.client.get(reverse("reports:sales"))
        for index in range(12):
            self.create_sale(number=f"QUERY-{index:02d}")
        with CaptureQueriesContext(connection) as large_context:
            self.client.get(reverse("reports:sales"))
        self.assertLessEqual(len(large_context), len(small_context) + 1)

    def test_csv_prevents_spreadsheet_formula_injection_and_tenant_leakage(self):
        self.product.name = "=DANGEROUS()"
        self.product.save(update_fields=["name", "updated_at"])
        self.create_sale(number="SAFE")
        self.create_sale(number="SECRET", product=self.other_product)
        response = self.client.get(reverse("reports:products-export"))
        content = response.content.decode()
        self.assertIn("'=DANGEROUS()", content)
        self.assertNotIn("Secret product", content)
