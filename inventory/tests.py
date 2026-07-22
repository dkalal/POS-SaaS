from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from accounts.models import TenantMembership
from catalog.models import Category, Product
from core.exceptions import InsufficientStockError, PermissionDeniedError, StockAdjustmentAlreadyPostedError
from inventory.models import Stock, StockAdjustment, StockMovement
from inventory.services import cancel_adjustment, create_draft_adjustment, post_adjustment, update_draft_adjustment
from tenants.models import Tenant


User = get_user_model()


class StockAdjustmentServiceTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.user = User.objects.create_user(username="manager", password="pass12345")
        self.owner = User.objects.create_user(username="owner", password="pass12345")
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.user,
            role=TenantMembership.Role.MANAGER,
            is_active=True,
        )
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.owner,
            role=TenantMembership.Role.OWNER_ADMIN,
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
        self.other = Product.objects.create(
            tenant=self.tenant,
            category=self.category,
            name="Switch",
            sku="SWT-001",
            barcode="2222222222222",
            cost_price=Decimal("25.00"),
            sale_price=Decimal("40.00"),
            is_active=True,
        )
        Stock.objects.create(
            tenant=self.tenant,
            product=self.product,
            quantity=10,
            cost_value=Decimal("500.00"),
        )

    def test_create_draft_adjustment(self):
        adjustment = create_draft_adjustment(
            tenant=self.tenant,
            reason="Cycle count",
            notes="Shelf A",
            items=[{"product": self.product, "direction": "increase", "quantity": 2, "note": "Found extra"}],
            created_by=self.user,
        )

        self.assertEqual(adjustment.status, StockAdjustment.Status.DRAFT)
        self.assertEqual(adjustment.items.count(), 1)
        item = adjustment.items.get()
        self.assertEqual(item.quantity_delta, 2)
        self.assertEqual(item.quantity_before, 10)
        self.assertEqual(item.quantity_after, 12)

    def test_update_draft_adjustment_replaces_lines(self):
        adjustment = create_draft_adjustment(
            tenant=self.tenant,
            reason="Cycle count",
            items=[{"product": self.product, "direction": "increase", "quantity": 1, "note": "Found"}],
            created_by=self.user,
        )

        updated = update_draft_adjustment(
            adjustment_id=adjustment.id,
            tenant=self.tenant,
            reason="Updated count",
            notes="Adjusted notes",
            items=[{"product": self.other, "direction": "decrease", "quantity": 1, "note": "Missing"}],
            updated_by=self.user,
        )

        self.assertEqual(updated.reason, "Updated count")
        self.assertEqual(updated.items.count(), 1)
        self.assertEqual(updated.items.get().product, self.other)

    def test_post_adjustment_updates_stock_and_writes_movements(self):
        adjustment = create_draft_adjustment(
            tenant=self.tenant,
            reason="Cycle count",
            items=[
                {"product": self.product, "direction": "increase", "quantity": 2, "note": "Found"},
                {"product": self.other, "direction": "increase", "quantity": 3, "note": "New shelf"},
            ],
            created_by=self.user,
        )

        posted = post_adjustment(adjustment.id, self.owner)

        self.assertEqual(posted.status, StockAdjustment.Status.POSTED)
        self.assertEqual(Stock.objects.get(tenant=self.tenant, product=self.product).quantity, 12)
        self.assertEqual(Stock.objects.get(tenant=self.tenant, product=self.other).quantity, 3)
        self.assertEqual(StockMovement.objects.filter(reference_id=adjustment.id).count(), 2)

    def test_cancel_posted_adjustment_reverses_stock(self):
        adjustment = create_draft_adjustment(
            tenant=self.tenant,
            reason="Cycle count",
            items=[{"product": self.product, "direction": "increase", "quantity": 2, "note": "Found"}],
            created_by=self.user,
        )
        post_adjustment(adjustment.id, self.owner)

        cancelled = cancel_adjustment(adjustment.id, self.owner, reason="Wrong count")

        self.assertEqual(cancelled.status, StockAdjustment.Status.CANCELLED)
        self.assertEqual(Stock.objects.get(tenant=self.tenant, product=self.product).quantity, 10)
        self.assertEqual(StockMovement.objects.filter(reference_id=adjustment.id).count(), 2)
        self.assertEqual(StockMovement.objects.order_by("id").last().movement_type, StockMovement.MovementType.ADJUSTMENT_OUT)

    def test_post_adjustment_rejects_negative_stock(self):
        adjustment = create_draft_adjustment(
            tenant=self.tenant,
            reason="Bad count",
            items=[{"product": self.product, "direction": "decrease", "quantity": 20, "note": "Missing"}],
            created_by=self.user,
        )

        with self.assertRaises(InsufficientStockError):
            post_adjustment(adjustment.id, self.owner)

    def test_manager_cannot_post_or_cancel_adjustments(self):
        adjustment = create_draft_adjustment(
            tenant=self.tenant,
            reason="Cycle count",
            items=[{"product": self.product, "direction": "increase", "quantity": 2, "note": "Found"}],
            created_by=self.user,
        )

        with self.assertRaises(PermissionDeniedError):
            post_adjustment(adjustment.id, self.user)

        post_adjustment(adjustment.id, self.owner)

        with self.assertRaises(PermissionDeniedError):
            cancel_adjustment(adjustment.id, self.user, reason="Not approved")


class StockAdjustmentUiTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.user = User.objects.create_user(username="manager", password="pass12345")
        self.cashier = User.objects.create_user(username="cashier", password="pass12345")
        self.owner = User.objects.create_user(username="owner", password="pass12345")
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.user,
            role=TenantMembership.Role.MANAGER,
            is_active=True,
        )
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.owner,
            role=TenantMembership.Role.OWNER_ADMIN,
            is_active=True,
        )
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
            name="Router",
            sku="RTR-001",
            barcode="1111111111111",
            cost_price=Decimal("50.00"),
            sale_price=Decimal("80.00"),
            is_active=True,
        )
        Stock.objects.create(
            tenant=self.tenant,
            product=self.product,
            quantity=10,
            cost_value=Decimal("500.00"),
        )

        self.client.force_login(self.user)
        session = self.client.session
        session["current_tenant_id"] = self.tenant.id
        session.save()

    def test_adjustment_create_edit_and_post(self):
        response = self.client.get(reverse("inventory:adjustment-create"))
        self.assertEqual(response.status_code, 200)

        created = self.client.post(
            reverse("inventory:adjustment-create"),
            data={
                "reason": "Cycle count",
                "notes": "Shelf A",
                "form-TOTAL_FORMS": "5",
                "form-INITIAL_FORMS": "0",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-product": self.product.id,
                "form-0-direction": "increase",
                "form-0-quantity": "2",
                "form-0-note": "Found extras",
            },
        )
        self.assertEqual(created.status_code, 302)
        adjustment = StockAdjustment.objects.get(tenant=self.tenant)
        self.assertEqual(adjustment.status, StockAdjustment.Status.DRAFT)

        edited = self.client.post(
            reverse("inventory:adjustment-edit", args=[adjustment.id]),
            data={
                "reason": "Cycle count revised",
                "notes": "Updated notes",
                "form-TOTAL_FORMS": "5",
                "form-INITIAL_FORMS": "1",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-product": self.product.id,
                "form-0-direction": "increase",
                "form-0-quantity": "3",
                "form-0-note": "Recounted",
            },
        )
        self.assertEqual(edited.status_code, 302)

        posted = self.client.post(
            reverse("inventory:adjustment-detail", args=[adjustment.id]),
            data={"action": "post"},
        )
        self.assertEqual(posted.status_code, 302)
        adjustment.refresh_from_db()
        self.assertEqual(adjustment.status, StockAdjustment.Status.DRAFT)

        self.client.force_login(self.user)
        session = self.client.session
        session["current_tenant_id"] = self.tenant.id
        session.save()

        posted = self.client.post(
            reverse("inventory:adjustment-detail", args=[adjustment.id]),
            data={"action": "post"},
        )
        self.assertEqual(posted.status_code, 302)
        adjustment.refresh_from_db()
        self.assertEqual(adjustment.status, StockAdjustment.Status.DRAFT)

        self.client.force_login(self.owner)
        session = self.client.session
        session["current_tenant_id"] = self.tenant.id
        session.save()

        posted = self.client.post(
            reverse("inventory:adjustment-detail", args=[adjustment.id]),
            data={"action": "post"},
        )
        self.assertEqual(posted.status_code, 302)
        adjustment.refresh_from_db()
        self.assertEqual(adjustment.status, StockAdjustment.Status.POSTED)
        self.assertEqual(Stock.objects.get(tenant=self.tenant, product=self.product).quantity, 13)

    def test_cashier_cannot_open_adjustments(self):
        self.client.force_login(self.cashier)
        session = self.client.session
        session["current_tenant_id"] = self.tenant.id
        session.save()

        response = self.client.get(reverse("inventory:adjustment-list"))
        self.assertEqual(response.status_code, 403)


class InventoryVisibilityTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="inventory-owner", password="pass12345")
        self.cashier = User.objects.create_user(username="inventory-cashier", password="pass12345")
        self.tenant = Tenant.objects.create(name="Inventory A", slug="inventory-a")
        self.other_tenant = Tenant.objects.create(name="Inventory B", slug="inventory-b")
        TenantMembership.objects.create(tenant=self.tenant, user=self.owner, role=TenantMembership.Role.OWNER_ADMIN, is_active=True)
        TenantMembership.objects.create(tenant=self.tenant, user=self.cashier, role=TenantMembership.Role.CASHIER, is_active=True)
        self.category = Category.objects.create(tenant=self.tenant, name="Hardware", slug="hardware", is_active=True)
        self.in_stock = Product.objects.create(tenant=self.tenant, category=self.category, name="In stock router", sku="RTR-100", barcode="100", reorder_level=3, is_active=True)
        self.low_stock = Product.objects.create(tenant=self.tenant, category=self.category, name="Low stock switch", sku="SWT-200", barcode="200", reorder_level=3, is_active=True)
        self.out_of_stock = Product.objects.create(tenant=self.tenant, category=self.category, name="Out stock cable", sku="CBL-300", barcode="300", reorder_level=2, is_active=True)
        self.service = Product.objects.create(tenant=self.tenant, category=self.category, name="Installation", sku="SRV-400", track_inventory=False, is_active=True)
        self.stock = Stock.objects.create(tenant=self.tenant, product=self.in_stock, quantity=7)
        Stock.objects.create(tenant=self.tenant, product=self.low_stock, quantity=2)
        Stock.objects.create(tenant=self.tenant, product=self.out_of_stock, quantity=0)
        StockMovement.objects.create(tenant=self.tenant, stock=self.stock, product=self.in_stock, movement_type=StockMovement.MovementType.PURCHASE_IN, reference_type=StockMovement.ReferenceType.PURCHASE, reference_id=42, quantity_delta=7, quantity_before=0, quantity_after=7, created_by=self.owner, note="Initial receiving")
        self.other_product = Product.objects.create(tenant=self.other_tenant, name="Other tenant item", sku="OTH-001", is_active=True)
        self.other_stock = Stock.objects.create(tenant=self.other_tenant, product=self.other_product, quantity=9)
        StockMovement.objects.create(tenant=self.other_tenant, stock=self.other_stock, product=self.other_product, movement_type=StockMovement.MovementType.PURCHASE_IN, reference_type=StockMovement.ReferenceType.PURCHASE, reference_id=99, quantity_delta=9, quantity_before=0, quantity_after=9, created_by=self.owner)
        self.client.force_login(self.owner)
        session = self.client.session
        session["current_tenant_id"] = self.tenant.id
        session.save()

    def test_overview_is_tenant_scoped_and_excludes_services(self):
        response = self.client.get(reverse("inventory:overview"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "In stock router")
        self.assertNotContains(response, "Installation")
        self.assertNotContains(response, "Other tenant item")
        self.assertContains(response, "Low stock")
        self.assertContains(response, "Out of stock")

    def test_inventory_search_uses_sku_name_and_barcode(self):
        for query in ("RTR-100", "switch", "300"):
            response = self.client.get(reverse("inventory:overview"), {"q": query})
            self.assertEqual(response.status_code, 200)
        self.assertContains(self.client.get(reverse("inventory:overview"), {"q": "300"}), "Out stock cable")

    def test_ledger_and_history_are_read_only_and_tenant_scoped(self):
        ledger = self.client.get(reverse("inventory:movement-ledger"))
        self.assertContains(ledger, "Initial receiving")
        self.assertNotContains(ledger, "Other tenant item")
        history = self.client.get(reverse("inventory:product-history", args=[self.in_stock.id]))
        self.assertContains(history, "Purchase In")
        self.assertNotContains(history, "Edit")
        self.assertEqual(self.client.get(reverse("inventory:product-history", args=[self.other_product.id])).status_code, 404)

    def test_cashier_cannot_view_inventory_or_ledger(self):
        self.client.force_login(self.cashier)
        session = self.client.session
        session["current_tenant_id"] = self.tenant.id
        session.save()
        self.assertEqual(self.client.get(reverse("inventory:overview")).status_code, 403)
        self.assertEqual(self.client.get(reverse("inventory:movement-ledger")).status_code, 403)
