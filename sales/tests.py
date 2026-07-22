from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.test import Client
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Category, Product
from accounts.models import TenantMembership
from core.exceptions import InsufficientStockError, PaymentMethodNotAllowedError, SaleAlreadyCancelledError
from inventory.models import Stock, StockMovement
from payments.models import Payment
from sales.models import Customer, Invoice, Quotation, QuotationItem, Receipt, Sale, SaleItem
from sales.services import (
    calculate_sale_totals,
    cancel_sale,
    complete_sale,
    convert_quotation_to_invoice,
    save_quotation,
)
from tenants.models import Tenant


User = get_user_model()


class SalesServiceTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        self.cashier = User.objects.create_user(username="cashier", password="pass12345")
        self.manager = User.objects.create_user(username="manager", password="pass12345")
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.cashier,
            role=TenantMembership.Role.CASHIER,
            is_active=True,
        )
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.manager,
            role=TenantMembership.Role.MANAGER,
            is_active=True,
        )
        self.category = Category.objects.create(
            tenant=self.tenant,
            name="Devices",
            slug="devices",
            is_active=True,
        )
        self.product = Product.objects.create(
            tenant=self.tenant,
            category=self.category,
            name="USB Cable",
            sku="USB-001",
            barcode="2222222222222",
            cost_price=Decimal("10.00"),
            sale_price=Decimal("15.00"),
            is_active=True,
        )
        self.stock = Stock.objects.create(
            tenant=self.tenant,
            product=self.product,
            quantity=5,
            cost_value=Decimal("50.00"),
        )

    def test_calculate_sale_totals(self):
        totals = calculate_sale_totals(
            [{"quantity": 2, "unit_price": Decimal("10.155")}],
            discount=Decimal("1.00"),
            tax=Decimal("0.50"),
        )

        self.assertEqual(totals["subtotal"], Decimal("20.32"))
        self.assertEqual(totals["discount"], Decimal("1.00"))
        self.assertEqual(totals["tax"], Decimal("0.50"))
        self.assertEqual(totals["grand_total"], Decimal("19.82"))

    def test_complete_sale_success(self):
        sale = complete_sale(
            tenant=self.tenant,
            cashier=self.cashier,
            cart_items=[{"product": self.product, "quantity": 2, "unit_price": Decimal("15.00")}],
            payment_method=Payment.Method.CASH,
            discount=Decimal("1.00"),
            tax=Decimal("0.50"),
        )

        stock = Stock.objects.get(pk=self.stock.pk)
        sale_item = SaleItem.objects.get(sale=sale)
        payment = Payment.objects.get(sale=sale)
        movement = StockMovement.objects.get(reference_id=sale.id)

        self.assertEqual(sale.subtotal, Decimal("30.00"))
        self.assertEqual(sale.grand_total, Decimal("29.50"))
        self.assertEqual(stock.quantity, 3)
        self.assertEqual(sale_item.unit_cost_snapshot, Decimal("10.00"))
        self.assertEqual(payment.amount, Decimal("29.50"))
        self.assertEqual(movement.movement_type, StockMovement.MovementType.SALE_OUT)
        self.assertEqual(Receipt.objects.filter(sale=sale).count(), 1)

    def test_complete_sale_records_payment_reference_and_skips_stock_for_services(self):
        service = Product.objects.create(
            tenant=self.tenant,
            category=self.category,
            name="Setup service",
            sku="SETUP-001",
            cost_price=Decimal("0.00"),
            sale_price=Decimal("25.00"),
            track_inventory=False,
            is_active=True,
        )

        sale = complete_sale(
            tenant=self.tenant,
            cashier=self.cashier,
            cart_items=[{"product": service, "quantity": 1, "unit_price": Decimal("25.00")}],
            payment_method=Payment.Method.MOBILE_MONEY,
            reference="MOBILE-REF-42",
        )

        self.assertEqual(Payment.objects.get(sale=sale).reference, "MOBILE-REF-42")
        self.assertFalse(Stock.objects.filter(tenant=self.tenant, product=service).exists())
        self.assertFalse(StockMovement.objects.filter(tenant=self.tenant, product=service).exists())

    def test_quotation_conversion_creates_draft_invoice_without_stock_change(self):
        quotation = Quotation.objects.create(
            tenant=self.tenant, quotation_number="Q-1", subtotal=Decimal("15.00"),
            grand_total=Decimal("15.00"), created_by=self.manager,
        )
        QuotationItem.objects.create(
            tenant=self.tenant, quotation=quotation, product=self.product, quantity=1,
            unit_price=Decimal("15.00"), line_total=Decimal("15.00"),
        )
        invoice = convert_quotation_to_invoice(quotation=quotation, actor=self.manager)
        quotation.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        self.assertEqual(quotation.status, Quotation.Status.CONVERTED)
        self.assertEqual(self.stock.quantity, 5)
        self.assertFalse(StockMovement.objects.exists())

    def test_paying_the_same_draft_invoice_is_idempotent(self):
        quotation = Quotation.objects.create(
            tenant=self.tenant, quotation_number="Q-PAID-1", subtotal=Decimal("15.00"),
            grand_total=Decimal("15.00"), created_by=self.manager,
        )
        QuotationItem.objects.create(tenant=self.tenant, quotation=quotation, product=self.product,
                                     quantity=1, unit_price=Decimal("15.00"), line_total=Decimal("15.00"))
        invoice = convert_quotation_to_invoice(quotation=quotation, actor=self.manager)
        line_items = [{"product": self.product, "quantity": 1, "unit_price": Decimal("15.00")}]
        first = complete_sale(tenant=self.tenant, cashier=self.cashier, cart_items=line_items,
                              payment_method=Payment.Method.CASH, invoice=invoice, checkout_key="invoice-pay")
        second = complete_sale(tenant=self.tenant, cashier=self.cashier, cart_items=line_items,
                               payment_method=Payment.Method.CASH, invoice=invoice, checkout_key="another-retry")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(Receipt.objects.count(), 1)

    def test_complete_sale_concurrent_attempts_only_one_succeeds(self):
        barrier = Barrier(2)

        def worker():
            close_old_connections()
            barrier.wait()
            try:
                complete_sale(
                    tenant=self.tenant,
                    cashier=self.cashier,
                    cart_items=[{"product": self.product, "quantity": 5, "unit_price": Decimal("15.00")}],
                    payment_method=Payment.Method.CASH,
                )
                return "success"
            except InsufficientStockError:
                return "insufficient_stock"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: worker(), range(2)))

        stock = Stock.objects.get(pk=self.stock.pk)
        self.assertEqual(results.count("success"), 1)
        self.assertEqual(results.count("insufficient_stock"), 1)
        self.assertEqual(stock.quantity, 0)
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(StockMovement.objects.filter(reference_id__isnull=False).count(), 1)

    def test_complete_sale_rolls_back_when_movement_write_fails(self):
        with patch("inventory.models.StockMovement.objects.create", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                complete_sale(
                    tenant=self.tenant,
                    cashier=self.cashier,
                    cart_items=[{"product": self.product, "quantity": 2, "unit_price": Decimal("15.00")}],
                    payment_method=Payment.Method.CASH,
                )

        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 5)
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(SaleItem.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertEqual(Receipt.objects.count(), 0)

    def test_cancel_sale_success(self):
        sale = complete_sale(
            tenant=self.tenant,
            cashier=self.cashier,
            cart_items=[{"product": self.product, "quantity": 2, "unit_price": Decimal("15.00")}],
            payment_method=Payment.Method.CASH,
        )

        cancelled = cancel_sale(sale.id, self.manager, "customer returned item")

        self.stock.refresh_from_db()
        self.assertEqual(cancelled.status, Sale.Status.CANCELLED)
        self.assertEqual(self.stock.quantity, 5)
        self.assertEqual(StockMovement.objects.filter(reference_id=sale.id).count(), 2)

    def test_cancel_already_cancelled_sale_raises(self):
        sale = complete_sale(
            tenant=self.tenant,
            cashier=self.cashier,
            cart_items=[{"product": self.product, "quantity": 1, "unit_price": Decimal("15.00")}],
            payment_method=Payment.Method.CASH,
        )
        cancel_sale(sale.id, self.manager, "first cancel")

        with self.assertRaises(SaleAlreadyCancelledError):
            cancel_sale(sale.id, self.manager, "second cancel")


class SalesRegisterViewTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.client = Client()
        self.tenant = Tenant.objects.create(name="Tenant C", slug="tenant-c")
        self.cashier = User.objects.create_user(username="cashier2", password="pass12345")
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.cashier,
            role=TenantMembership.Role.MANAGER,
            is_active=True,
        )
        self.category = Category.objects.create(
            tenant=self.tenant,
            name="Accessories",
            slug="accessories",
            is_active=True,
        )
        self.product = Product.objects.create(
            tenant=self.tenant,
            category=self.category,
            name="USB Adapter",
            sku="USB-ADAPT",
            barcode="3333333333333",
            cost_price=Decimal("4.00"),
            sale_price=Decimal("9.50"),
            is_active=True,
        )
        Stock.objects.create(
            tenant=self.tenant,
            product=self.product,
            quantity=4,
            cost_value=Decimal("16.00"),
        )
        self.client.force_login(self.cashier)

    def test_register_page_renders_for_cashier(self):
        response = self.client.get(reverse("sales:register"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Find products")
        self.assertContains(response, "USB Adapter")
        self.assertContains(response, 'class="register-search-field"')
        self.assertContains(response, "Take payment")
        self.assertContains(response, "data-checkout-trigger")

    def test_register_add_preserves_active_catalog_search(self):
        register_url = f'{reverse("sales:register")}?q=USB&category={self.category.id}'

        response = self.client.post(
            register_url,
            {"action": "add", "product_id": self.product.id},
        )

        self.assertRedirects(response, register_url, fetch_redirect_response=False)

    def test_register_adds_item_and_completes_sale(self):
        response = self.client.post(reverse("sales:register"), {"action": "add", "product_id": self.product.id})
        self.assertEqual(response.status_code, 302)

        response = self.client.post(
            reverse("sales:register"),
            {
                "action": "save-pricing",
                "discount": "1.00",
                "tax": "0.50",
            },
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.post(
            reverse("sales:register"),
            {
                "action": "complete-sale",
                "payment_method": Payment.Method.CASH,
                "reference": "ref-1",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(Stock.objects.get(pk=self.product.stock.pk).quantity, 3)
        self.assertEqual(Payment.objects.get().reference, "ref-1")

    def test_register_prevents_adding_more_than_available_stock(self):
        for _ in range(4):
            response = self.client.post(reverse("sales:register"), {"action": "add", "product_id": self.product.id})
            self.assertEqual(response.status_code, 302)

        response = self.client.post(reverse("sales:register"), {"action": "add", "product_id": self.product.id}, follow=True)

        self.assertContains(response, "has no more stock available to add")

    def test_register_search_prioritizes_exact_sku_then_name_then_barcode(self):
        name_match = Product.objects.create(
            tenant=self.tenant,
            category=self.category,
            name="MATCH",
            sku="NAME-MATCH",
            sale_price=Decimal("5.00"),
            is_active=True,
        )
        barcode_match = Product.objects.create(
            tenant=self.tenant,
            category=self.category,
            name="Barcode result",
            sku="BAR-MATCH",
            barcode="MATCH",
            sale_price=Decimal("6.00"),
            is_active=True,
        )
        sku_match = Product.objects.create(
            tenant=self.tenant,
            category=self.category,
            name="SKU result",
            sku="MATCH",
            sale_price=Decimal("7.00"),
            is_active=True,
        )

        response = self.client.get(reverse("sales:register"), {"q": "MATCH"})
        content = response.content.decode()
        content = content[content.index('class="register-product-grid"'):]

        self.assertLess(content.index(sku_match.name), content.index(name_match.name))
        self.assertLess(content.index(name_match.name), content.index(barcode_match.name))

    def test_register_category_filter_stays_tenant_scoped(self):
        other_category = Category.objects.create(
            tenant=self.tenant,
            name="Services",
            slug="services",
            is_active=True,
        )
        Product.objects.create(
            tenant=self.tenant,
            category=other_category,
            name="Installation",
            sku="INSTALL-1",
            sale_price=Decimal("12.00"),
            track_inventory=False,
            is_active=True,
        )
        other_tenant = Tenant.objects.create(name="Tenant D", slug="tenant-d")
        foreign_category = Category.objects.create(
            tenant=other_tenant,
            name="Foreign",
            slug="foreign",
            is_active=True,
        )
        foreign_product = Product.objects.create(
            tenant=other_tenant,
            category=foreign_category,
            name="Foreign product",
            sku="FOREIGN-1",
            sale_price=Decimal("20.00"),
            is_active=True,
        )

        response = self.client.get(reverse("sales:register"), {"category": other_category.id})

        self.assertContains(response, "Installation")
        self.assertNotContains(response, self.product.name)
        self.assertNotContains(response, foreign_product.name)

    def test_document_list_pages_render_with_queryset_search(self):
        sale = complete_sale(
            tenant=self.tenant,
            cashier=self.cashier,
            cart_items=[{"product": self.product, "quantity": 1, "unit_price": Decimal("9.50")}],
            payment_method=Payment.Method.CASH,
        )
        quotation = Quotation.objects.create(
            tenant=self.tenant,
            quotation_number="Q-USB-1",
            subtotal=Decimal("9.50"),
            grand_total=Decimal("9.50"),
            created_by=self.cashier,
        )
        QuotationItem.objects.create(
            tenant=self.tenant,
            quotation=quotation,
            product=self.product,
            quantity=1,
            unit_price=Decimal("9.50"),
            line_total=Decimal("9.50"),
        )

        urls = [
            reverse("sales:sale-list"),
            reverse("sales:invoice-list"),
            reverse("sales:quotation-list"),
            reverse("sales:receipt-list"),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url, {"q": "USB"})
                self.assertEqual(response.status_code, 200)

        self.assertEqual(Receipt.objects.filter(sale=sale).count(), 1)

    def test_sales_history_shows_confirmed_units_not_line_count(self):
        self.client.post(reverse("sales:register"), {"action": "add", "product_id": self.product.id})
        self.client.post(
            reverse("sales:register"),
            {"action": "update-line", "product_id": self.product.id, "quantity": 3},
        )
        self.client.post(
            reverse("sales:register"),
            {"action": "complete-sale", "payment_method": Payment.Method.CASH},
        )

        sale = Sale.objects.get()
        response = self.client.get(reverse("sales:sale-list"))

        self.assertContains(response, "Units sold")
        self.assertContains(response, f"<td>{sale.items.get().quantity}</td>", html=False)


class CashierDocumentScopeTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Cashier Scope", slug="cashier-scope")
        self.cashier = User.objects.create_user(username="register-cashier", password="pass12345")
        self.other_cashier = User.objects.create_user(username="other-register-cashier", password="pass12345")
        TenantMembership.objects.create(tenant=self.tenant, user=self.cashier, role=TenantMembership.Role.CASHIER)
        TenantMembership.objects.create(tenant=self.tenant, user=self.other_cashier, role=TenantMembership.Role.CASHIER)
        category = Category.objects.create(tenant=self.tenant, name="Scope category", slug="scope-category")
        self.product = Product.objects.create(
            tenant=self.tenant,
            category=category,
            name="Scope product",
            sku="SCOPE-001",
            cost_price=Decimal("4.00"),
            sale_price=Decimal("10.00"),
            is_active=True,
        )
        Stock.objects.create(tenant=self.tenant, product=self.product, quantity=10, cost_value=Decimal("40.00"))
        self.my_customer = Customer.objects.create(tenant=self.tenant, name="My customer")
        self.other_customer = Customer.objects.create(tenant=self.tenant, name="Other cashier customer")
        self.my_sale = complete_sale(
            tenant=self.tenant,
            cashier=self.cashier,
            customer=self.my_customer,
            cart_items=[{"product": self.product, "quantity": 1, "unit_price": Decimal("10.00")}],
            payment_method=Payment.Method.CASH,
        )
        self.other_sale = complete_sale(
            tenant=self.tenant,
            cashier=self.other_cashier,
            customer=self.other_customer,
            cart_items=[{"product": self.product, "quantity": 1, "unit_price": Decimal("10.00")}],
            payment_method=Payment.Method.CASH,
        )
        self.client.force_login(self.cashier)

    def test_cashier_register_header_has_account_actions_not_dashboard_link(self):
        response = self.client.get(reverse("sales:register"))

        self.assertContains(response, "Open account menu")
        self.assertContains(response, "Sign out")
        self.assertContains(response, "My activity")
        self.assertContains(response, reverse("sales:sale-list"))
        self.assertContains(response, reverse("sales:invoice-list"))
        self.assertContains(response, reverse("sales:receipt-list"))
        self.assertNotContains(response, ">Dashboard<", html=False)
        self.assertNotContains(response, "Return to dashboard")

    def test_cashier_can_only_discover_and_open_own_sales_and_receipts(self):
        sales_response = self.client.get(reverse("sales:sale-list"))
        self.assertContains(sales_response, self.my_sale.sale_number)
        self.assertNotContains(sales_response, self.other_sale.sale_number)
        self.assertContains(sales_response, self.my_customer.name)
        self.assertNotContains(sales_response, self.other_customer.name)
        self.assertEqual(self.client.get(reverse("sales:sale-detail", args=[self.other_sale.id])).status_code, 404)

        my_receipt = self.my_sale.receipt
        other_receipt = self.other_sale.receipt
        receipts_response = self.client.get(reverse("sales:receipt-list"))
        self.assertContains(receipts_response, my_receipt.receipt_number)
        self.assertNotContains(receipts_response, other_receipt.receipt_number)
        self.assertEqual(self.client.get(reverse("sales:receipt-detail", args=[other_receipt.id])).status_code, 404)
        self.assertEqual(self.client.get(reverse("sales:receipt-print", args=[other_receipt.id])).status_code, 404)


class Phase6DocumentSecurityTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Phase Six A", slug="phase-six-a")
        self.other_tenant = Tenant.objects.create(name="Phase Six B", slug="phase-six-b")
        self.manager = User.objects.create_user(username="phase6-manager", password="pass12345")
        self.other_manager = User.objects.create_user(username="phase6-other", password="pass12345")
        self.viewer = User.objects.create_user(username="phase6-viewer", password="pass12345")
        TenantMembership.objects.create(tenant=self.tenant, user=self.manager, role=TenantMembership.Role.MANAGER)
        TenantMembership.objects.create(tenant=self.other_tenant, user=self.other_manager, role=TenantMembership.Role.MANAGER)
        TenantMembership.objects.create(tenant=self.tenant, user=self.viewer, role=TenantMembership.Role.VIEWER)
        self.category = Category.objects.create(tenant=self.tenant, name="Phase 6", slug="phase-6")
        self.product = Product.objects.create(
            tenant=self.tenant, category=self.category, name="Receipt Printer", sku="PRINT-01",
            cost_price=Decimal("40.00"), sale_price=Decimal("75.00"), is_active=True,
        )
        self.service = Product.objects.create(
            tenant=self.tenant, category=self.category, name="Installation", sku="SERVICE-01",
            sale_price=Decimal("20.00"), track_inventory=False, is_active=True,
        )
        self.stock = Stock.objects.create(tenant=self.tenant, product=self.product, quantity=3)
        self.customer = Customer.objects.create(tenant=self.tenant, name="Acme Retail")

    def select_workspace(self, user, tenant):
        self.client.force_login(user)
        session = self.client.session
        session["current_tenant_id"] = tenant.pk
        session.save()

    def make_sale(self):
        return complete_sale(
            tenant=self.tenant,
            cashier=self.manager,
            customer=self.customer,
            cart_items=[
                {"product": self.product, "quantity": 1, "unit_price": self.product.sale_price},
                {"product": self.service, "quantity": 1, "unit_price": self.service.sale_price},
            ],
            payment_method=Payment.Method.CASH,
            checkout_key="phase6-sale",
        )

    def make_quotation(self, **kwargs):
        return save_quotation(
            tenant=self.tenant,
            actor=self.manager,
            customer=self.customer,
            expires_at=kwargs.get("expires_at"),
            discount=Decimal("0.00"),
            tax=Decimal("0.00"),
            line_items=[{"product": self.product, "quantity": 1}],
        )

    def test_other_tenant_cannot_open_any_document_or_print_url(self):
        sale = self.make_sale()
        quotation = self.make_quotation()
        self.select_workspace(self.other_manager, self.other_tenant)
        urls = (
            reverse("sales:sale-detail", args=[sale.pk]),
            reverse("sales:invoice-detail", args=[sale.invoice.pk]),
            reverse("sales:quotation-detail", args=[quotation.pk]),
            reverse("sales:receipt-detail", args=[sale.receipt.pk]),
            reverse("sales:receipt-print", args=[sale.receipt.pk]),
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_viewer_is_blocked_from_lists_conversion_printing_and_payment(self):
        sale = self.make_sale()
        quotation = self.make_quotation()
        invoice = convert_quotation_to_invoice(quotation=quotation, actor=self.manager)
        self.select_workspace(self.viewer, self.tenant)
        requests = (
            ("get", reverse("sales:sale-list"), {}),
            ("get", reverse("sales:quotation-list"), {}),
            ("post", reverse("sales:quotation-detail", args=[quotation.pk]), {"action": "convert"}),
            ("post", reverse("sales:invoice-detail", args=[invoice.pk]), {"action": "confirm-payment", "payment_method": "cash"}),
            ("get", reverse("sales:receipt-print", args=[sale.receipt.pk]), {}),
        )
        for method, url, data in requests:
            with self.subTest(url=url):
                self.assertEqual(getattr(self.client, method)(url, data).status_code, 403)

    def test_draft_quotation_does_not_touch_stock_and_expired_offer_will_not_convert(self):
        quotation = self.make_quotation(expires_at=timezone.localdate() - timedelta(days=1))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 3)
        self.assertFalse(StockMovement.objects.exists())
        with self.assertRaisesMessage(ValueError, "expired"):
            convert_quotation_to_invoice(quotation=quotation, actor=self.manager)
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.Status.DRAFT)  # atomic rollback keeps the original offer unchanged
        self.assertFalse(Invoice.objects.exists())

    def test_invoice_full_payment_is_idempotent_and_receipt_follows_success_only(self):
        invoice = convert_quotation_to_invoice(quotation=self.make_quotation(), actor=self.manager)
        line_items = [{"product": item.product, "quantity": item.quantity, "unit_price": item.unit_price} for item in invoice.items.all()]
        sale = complete_sale(
            tenant=self.tenant, cashier=self.manager, cart_items=line_items,
            payment_method=Payment.Method.BANK_TRANSFER, invoice=invoice, checkout_key=f"invoice-{invoice.pk}",
        )
        retry = complete_sale(
            tenant=self.tenant, cashier=self.manager, cart_items=line_items,
            payment_method=Payment.Method.BANK_TRANSFER, invoice=invoice, checkout_key="retry-key",
        )
        self.assertEqual(retry.pk, sale.pk)
        self.assertEqual(Payment.objects.filter(sale=sale, amount=sale.grand_total).count(), 1)
        self.assertEqual(Receipt.objects.filter(sale=sale).count(), 1)
        self.assertEqual(StockMovement.objects.filter(reference_id=sale.pk).count(), 1)

    def test_failed_full_payment_leaves_invoice_draft_stock_and_documents_unchanged(self):
        invoice = convert_quotation_to_invoice(quotation=self.make_quotation(), actor=self.manager)
        self.stock.quantity = 0
        self.stock.save(update_fields=["quantity"])
        with self.assertRaises(InsufficientStockError):
            complete_sale(
                tenant=self.tenant, cashier=self.manager,
                cart_items=[{"product": self.product, "quantity": 1, "unit_price": self.product.sale_price}],
                payment_method=Payment.Method.CASH, invoice=invoice, checkout_key="failed-invoice",
            )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        self.assertFalse(Sale.objects.exists())
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(Receipt.objects.exists())
        with self.assertRaises(PaymentMethodNotAllowedError):
            complete_sale(
                tenant=self.tenant, cashier=self.manager,
                cart_items=[{"product": self.service, "quantity": 1, "unit_price": self.service.sale_price}],
                payment_method="partial", checkout_key="invalid-payment",
            )
        self.assertFalse(Payment.objects.exists())

    def test_quotation_form_uses_catalog_price_and_never_deducts_stock(self):
        self.select_workspace(self.manager, self.tenant)
        response = self.client.post(
            reverse("sales:quotation-create"),
            {
                "customer": self.customer.pk,
                "expires_at": "",
                "discount": "0.00",
                "tax": "0.00",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "50",
                "lines-0-product": self.product.pk,
                "lines-0-quantity": "2",
            },
        )
        self.assertEqual(response.status_code, 302)
        quotation = Quotation.objects.get()
        self.assertEqual(quotation.items.get().unit_price, self.product.sale_price)
        self.assertEqual(quotation.grand_total, Decimal("150.00"))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 3)
        self.assertFalse(StockMovement.objects.exists())

    def test_lists_search_filters_and_print_use_real_documents(self):
        sale = self.make_sale()
        self.select_workspace(self.manager, self.tenant)
        self.assertContains(self.client.get(reverse("sales:sale-list"), {"q": "PRINT-01"}), sale.sale_number)
        self.assertContains(self.client.get(reverse("sales:invoice-list"), {"q": sale.invoice.invoice_number}), sale.invoice.invoice_number)
        self.assertContains(self.client.get(reverse("sales:receipt-list"), {"payment_method": "cash"}), sale.receipt.receipt_number)
        print_response = self.client.get(reverse("sales:receipt-print", args=[sale.receipt.pk]))
        self.assertContains(print_response, sale.receipt.receipt_number)
        self.assertContains(print_response, "@media print")
        self.assertContains(print_response, "Paid")
        invoice_response = self.client.get(reverse("sales:invoice-detail", args=[sale.invoice.pk]))
        receipt_response = self.client.get(reverse("sales:receipt-detail", args=[sale.receipt.pk]))
        self.assertNotContains(invoice_response, "Confirm full payment")
        self.assertNotContains(receipt_response, 'name="amount"')
