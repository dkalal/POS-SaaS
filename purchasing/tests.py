from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from catalog.models import Category, Product
from core.exceptions import InsufficientStockError, PurchaseAlreadyReceivedError
from accounts.models import TenantMembership
from inventory.models import Stock, StockMovement
from purchasing.models import Purchase, PurchaseItem
from purchasing.services import cancel_received_purchase, create_draft_purchase, duplicate_purchase, receive_purchase, update_draft_purchase
from suppliers.models import Supplier
from tenants.models import Tenant


User = get_user_model()


class PurchasingServiceTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.user = User.objects.create_user(username="owner", password="pass12345")
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.user,
            role=TenantMembership.Role.MANAGER,
            is_active=True,
        )
        self.supplier = Supplier.objects.create(
            tenant=self.tenant,
            name="Main Supplier",
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
            name="Router",
            sku="RTR-001",
            barcode="1111111111111",
            cost_price=Decimal("50.00"),
            sale_price=Decimal("80.00"),
            is_active=True,
        )

    def test_create_draft_purchase(self):
        purchase = create_draft_purchase(
            tenant=self.tenant,
            supplier=self.supplier,
            items=[{"product": self.product, "quantity": 2, "unit_cost": Decimal("45.50")}],
            created_by=self.user,
            order_date=date(2026, 7, 1),
            expected_date=date(2026, 7, 5),
            notes="First order",
        )

        self.assertEqual(purchase.status, Purchase.Status.DRAFT)
        self.assertEqual(purchase.items.count(), 1)
        item = purchase.items.get()
        self.assertEqual(item.line_total, Decimal("91.00"))
        self.assertEqual(item.unit_cost, Decimal("45.50"))
        self.assertEqual(purchase.order_date, date(2026, 7, 1))
        self.assertEqual(purchase.expected_date, date(2026, 7, 5))
        self.assertEqual(purchase.notes, "First order")

    def test_update_draft_purchase_replaces_lines(self):
        purchase = create_draft_purchase(
            tenant=self.tenant,
            supplier=self.supplier,
            items=[{"product": self.product, "quantity": 1, "unit_cost": Decimal("45.00")}],
            created_by=self.user,
        )
        updated = update_draft_purchase(
            purchase_id=purchase.id,
            tenant=self.tenant,
            supplier=self.supplier,
            items=[{"product": self.product, "quantity": 3, "unit_cost": Decimal("47.00")}],
            updated_by=self.user,
            notes="Revised",
        )

        self.assertEqual(updated.items.count(), 1)
        self.assertEqual(updated.items.get().quantity, 3)
        self.assertEqual(updated.items.get().unit_cost, Decimal("47.00"))
        self.assertEqual(updated.notes, "Revised")

    def test_duplicate_purchase_copies_header_and_items(self):
        source = create_draft_purchase(
            tenant=self.tenant,
            supplier=self.supplier,
            items=[{"product": self.product, "quantity": 2, "unit_cost": Decimal("45.50")}],
            created_by=self.user,
            expected_date=date(2026, 7, 9),
            notes="Copy me",
        )

        duplicate = duplicate_purchase(source.id, self.user)

        self.assertNotEqual(duplicate.purchase_number, source.purchase_number)
        self.assertEqual(duplicate.supplier, source.supplier)
        self.assertEqual(duplicate.expected_date, date(2026, 7, 9))
        self.assertEqual(duplicate.notes, "Copy me")
        self.assertEqual(duplicate.items.count(), 1)

    def test_receive_purchase_success(self):
        purchase = create_draft_purchase(
            tenant=self.tenant,
            supplier=self.supplier,
            items=[{"product": self.product, "quantity": 3, "unit_cost": Decimal("45.00")}],
            created_by=self.user,
        )

        received = receive_purchase(purchase.id, self.user)

        stock = Stock.objects.get(tenant=self.tenant, product=self.product)
        movement = StockMovement.objects.get(tenant=self.tenant, reference_id=purchase.id)

        self.assertEqual(received.status, Purchase.Status.RECEIVED)
        self.assertEqual(received.received_by, self.user)
        self.assertEqual(stock.quantity, 3)
        self.assertEqual(movement.movement_type, StockMovement.MovementType.PURCHASE_IN)
        self.assertEqual(movement.quantity_before, 0)
        self.assertEqual(movement.quantity_after, 3)

    def test_receive_purchase_concurrent_attempts_only_apply_once(self):
        purchase = create_draft_purchase(
            tenant=self.tenant,
            supplier=self.supplier,
            items=[{"product": self.product, "quantity": 1, "unit_cost": Decimal("45.00")}],
            created_by=self.user,
        )
        Stock.objects.create(
            tenant=self.tenant,
            product=self.product,
            quantity=0,
            cost_value=Decimal("0.00"),
        )

        barrier = Barrier(2)

        def worker():
            close_old_connections()
            barrier.wait()
            try:
                receive_purchase(purchase.id, self.user)
                return "success"
            except PurchaseAlreadyReceivedError:
                return "already_received"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: worker(), range(2)))

        stock = Stock.objects.get(tenant=self.tenant, product=self.product)
        self.assertEqual(results.count("success"), 1)
        self.assertEqual(results.count("already_received"), 1)
        self.assertEqual(stock.quantity, 1)
        self.assertEqual(StockMovement.objects.filter(reference_id=purchase.id).count(), 1)

    def test_receive_purchase_rolls_back_when_movement_write_fails(self):
        purchase = create_draft_purchase(
            tenant=self.tenant,
            supplier=self.supplier,
            items=[{"product": self.product, "quantity": 4, "unit_cost": Decimal("45.00")}],
            created_by=self.user,
        )

        with patch("inventory.models.StockMovement.objects.create", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                receive_purchase(purchase.id, self.user)

        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.DRAFT)
        self.assertFalse(Stock.objects.filter(tenant=self.tenant, product=self.product).exists())
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_cancel_received_purchase_success(self):
        purchase = create_draft_purchase(
            tenant=self.tenant,
            supplier=self.supplier,
            items=[{"product": self.product, "quantity": 5, "unit_cost": Decimal("45.00")}],
            created_by=self.user,
        )
        receive_purchase(purchase.id, self.user)

        cancelled = cancel_received_purchase(purchase.id, self.user)

        stock = Stock.objects.get(tenant=self.tenant, product=self.product)
        movements = StockMovement.objects.filter(reference_id=purchase.id).order_by("id")

        self.assertEqual(cancelled.status, Purchase.Status.CANCELLED)
        self.assertEqual(stock.quantity, 0)
        self.assertEqual(movements.count(), 2)
        self.assertEqual(movements.last().movement_type, StockMovement.MovementType.PURCHASE_REVERSAL)


class PurchasingUiTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.user = User.objects.create_user(username="manager", password="pass12345")
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.user,
            role=TenantMembership.Role.MANAGER,
            is_active=True,
        )
        self.supplier = Supplier.objects.create(
            tenant=self.tenant,
            name="Main Supplier",
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
            name="Router",
            sku="RTR-001",
            barcode="1111111111111",
            cost_price=Decimal("50.00"),
            sale_price=Decimal("80.00"),
            is_active=True,
        )

        self.client.force_login(self.user)
        session = self.client.session
        session["current_tenant_id"] = self.tenant.id
        session.save()

    def test_purchase_create_screen_creates_draft_purchase(self):
        response = self.client.get(reverse("purchasing:purchase-create"))
        self.assertEqual(response.status_code, 200)

        created = self.client.post(
            reverse("purchasing:purchase-create"),
            data={
                "supplier": self.supplier.id,
                "order_date": "2026-07-01",
                "expected_date": "",
                "notes": "Office restock",
                "form-TOTAL_FORMS": "5",
                "form-INITIAL_FORMS": "0",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-product": self.product.id,
                "form-0-quantity": "2",
                "form-0-unit_cost": "44.50",
            },
        )

        self.assertEqual(created.status_code, 302)
        purchase = Purchase.objects.get(tenant=self.tenant)
        self.assertEqual(purchase.status, Purchase.Status.DRAFT)
        self.assertEqual(purchase.supplier, self.supplier)
        self.assertEqual(purchase.items.count(), 1)
        self.assertEqual(purchase.items.first().line_total, Decimal("89.00"))

    def test_purchase_edit_screen_updates_draft_purchase(self):
        purchase = create_draft_purchase(
            tenant=self.tenant,
            supplier=self.supplier,
            items=[{"product": self.product, "quantity": 2, "unit_cost": Decimal("44.50")}],
            created_by=self.user,
        )

        response = self.client.post(
            reverse("purchasing:purchase-edit", args=[purchase.id]),
            data={
                "supplier": self.supplier.id,
                "order_date": "2026-07-02",
                "expected_date": "2026-07-06",
                "notes": "Updated note",
                "form-TOTAL_FORMS": "5",
                "form-INITIAL_FORMS": "1",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-product": self.product.id,
                "form-0-quantity": "4",
                "form-0-unit_cost": "46.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        purchase.refresh_from_db()
        self.assertEqual(purchase.order_date, date(2026, 7, 2))
        self.assertEqual(purchase.expected_date, date(2026, 7, 6))
        self.assertEqual(purchase.notes, "Updated note")
        self.assertEqual(purchase.items.first().quantity, 4)

    def test_purchase_detail_receive_and_cancel_paths(self):
        purchase = create_draft_purchase(
            tenant=self.tenant,
            supplier=self.supplier,
            items=[{"product": self.product, "quantity": 2, "unit_cost": Decimal("44.50")}],
            created_by=self.user,
        )

        received = self.client.post(
            reverse("purchasing:purchase-detail", args=[purchase.id]),
            data={"action": "receive"},
        )
        self.assertEqual(received.status_code, 302)
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.RECEIVED)

        cancelled = self.client.post(
            reverse("purchasing:purchase-detail", args=[purchase.id]),
            data={"action": "cancel", "cancel_reason": "Supplier correction"},
        )
        self.assertEqual(cancelled.status_code, 302)
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.CANCELLED)
        self.assertEqual(purchase.cancelled_reason, "Supplier correction")

    def test_purchase_duplicate_redirects_to_edit_screen(self):
        purchase = create_draft_purchase(
            tenant=self.tenant,
            supplier=self.supplier,
            items=[{"product": self.product, "quantity": 2, "unit_cost": Decimal("44.50")}],
            created_by=self.user,
        )

        response = self.client.post(reverse("purchasing:purchase-duplicate", args=[purchase.id]))

        self.assertEqual(response.status_code, 302)
        duplicated_id = int(response.url.rstrip("/").split("/")[-2])
        duplicated = Purchase.objects.get(pk=duplicated_id)
        self.assertEqual(duplicated.status, Purchase.Status.DRAFT)
        self.assertEqual(duplicated.items.count(), 1)

    def test_purchase_list_filters(self):
        other = Supplier.objects.create(
            tenant=self.tenant,
            name="Backup Supplier",
            is_active=True,
        )
        main_purchase = create_draft_purchase(
            tenant=self.tenant,
            supplier=self.supplier,
            items=[{"product": self.product, "quantity": 1, "unit_cost": Decimal("40.00")}],
            created_by=self.user,
        )
        backup_purchase = create_draft_purchase(
            tenant=self.tenant,
            supplier=other,
            items=[{"product": self.product, "quantity": 1, "unit_cost": Decimal("41.00")}],
            created_by=self.user,
        )

        response = self.client.get(
            reverse("purchasing:purchase-list"),
            {"q": "Backup", "supplier": other.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Backup Supplier")
        self.assertContains(response, backup_purchase.purchase_number)
        self.assertNotContains(response, main_purchase.purchase_number)
