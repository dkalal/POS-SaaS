from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.test import Client
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase
from django.urls import reverse

from catalog.models import Category, Product
from accounts.models import TenantMembership
from core.exceptions import InsufficientStockError, SaleAlreadyCancelledError
from inventory.models import Stock, StockMovement
from payments.models import Payment
from sales.models import Receipt, Sale, SaleItem
from sales.services import calculate_sale_totals, cancel_sale, complete_sale
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
            role=TenantMembership.Role.CASHIER,
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
        self.assertContains(response, "Product catalog")
        self.assertContains(response, "USB Adapter")

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
